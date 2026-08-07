"""Smart Money Concepts long setup: sweep -> CHoCH -> higher low -> BOS.

THE PATTERN, AS A MACHINE
    1. SWEEP        a swing low trades below the prior swing low
    2. CHoCH        price then breaks the swing high that preceded the sweep
    3. HIGHER LOW   a confirmed swing low above the sweep low
    4. BOS          price breaks the CHoCH high -> this bar is the trigger

Entry is the CLOSE of the bar that completes step 4. Not the order block, not
the sweep low, not a limit somewhere in the retrace — the close of the bar on
which the pattern became true. That is the worst realistic fill and the only
one that needs no assumption about whether a resting order would have been
hit. Every prettier entry price is a claim about the past that the data cannot
support.

Stop is the higher low. Target is the range high the sweep was taken from —
the "TARGET" line on every version of this diagram.

WHAT MAKES THIS DIFFERENT FROM THE DIAGRAM
Structure is read only through `structure.known_at`, so a pivot counts only
once it could actually have been seen — k bars after it printed. On MU's 4H
bars the median gap between a pivot and its confirmation is 3.9%, and one
sweep low was 18.4% away by the time it was confirmable. Diagrams draw pivots
as if you knew them at the time. This does not.

NOTHING HERE PREDICTS ANYTHING. It reports that four measurable conditions
are true in sequence. Whether that sequence pays is a question for the
backtest, and the backtest has a random control for exactly this reason.
"""

from __future__ import annotations

from scanner.features import profile
from scanner.features import intraday_context
from scanner.features.intraday import IntradayBars
from scanner.features.structure import (
    DEFAULT_K,
    HIGH,
    LOW,
    Swing,
    known_at,
    swings,
)
from scanner.models import Frozen

#: Pivots older than this many bars are treated as irrelevant structure.
DEFAULT_LOOKBACK = 60

#: A setup needs somewhere to go: reward must be at least this multiple of risk.
MIN_REWARD_RISK = 1.0

#: Minimum stop distance, as a percentage of price.
#:
#: Not a taste preference — arithmetic. Transaction costs are charged against
#: PRICE while results are measured in R, so cost in R scales as 1/risk. A
#: 20bps round trip costs 0.1R on a 2%-wide stop and 0.4R on a 0.5% one, and
#: this pattern produces plenty of the latter. Measured on 30m S&P 500 data,
#: dropping setups with stops under 1.5% moved net expectancy after 20bps of
#: friction from -0.121R to +0.237R.
#:
#: The mechanism was predicted before the test; the exact threshold was not.
#: 1.0, 1.5 and 2.0 all improve things and should be read as "wide enough",
#: not as a tuned optimum.
MIN_RISK_PCT = 1.5


class Setup(Frozen):
    """One detected long setup, with the levels it implies."""

    symbol: str
    trigger_index: int
    sweep_index: int
    sweep_price: float
    choch_index: int
    choch_price: float
    higher_low_index: int
    higher_low_price: float
    entry_price: float
    stop_price: float
    target_price: float

    @property
    def risk(self) -> float:
        return self.entry_price - self.stop_price

    @property
    def reward(self) -> float:
        return self.target_price - self.entry_price

    @property
    def reward_risk(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0


def is_live_setup(bars: IntradayBars, setup: Setup) -> bool:
    """Whether the latest close has not already invalidated the setup.

    A freshness window alone is insufficient: price can hit the target or
    stop before the next scan. Such a setup must not become a late entry.
    """
    if not bars.closes:
        return False
    latest = bars.closes[-1]
    return setup.stop_price < latest < setup.target_price


def detect(
    bars: IntradayBars,
    index: int,
    *,
    k: int = DEFAULT_K,
    lookback: int = DEFAULT_LOOKBACK,
    min_reward_risk: float = MIN_REWARD_RISK,
    min_risk_pct: float = 0.0,
    points: tuple[Swing, ...] | None = None,
) -> Setup | None:
    """Test whether bar `index` completes the pattern. Point-in-time.

    Returns None unless all four steps hold and the trade has room to run.

    `points` is the full pivot list, passed in by `scan` so it is computed
    once per series rather than once per bar. It is filtered through
    `known_at` here either way — precomputing must never become a way to see
    a pivot early.
    """
    if index < 1 or index >= len(bars):
        return None

    all_points = swings(bars.highs, bars.lows, k) if points is None else points
    visible = [
        s for s in known_at(all_points, index, k) if s.index >= index - lookback
    ]
    lows = [s for s in visible if s.kind == LOW]
    highs = [s for s in visible if s.kind == HIGH]
    if len(lows) < 3 or len(highs) < 2:
        return None

    # 3. HIGHER LOW — the most recent confirmed low.
    higher_low = lows[-1]
    # 1. SWEEP — the low before it, which must have taken out the one before.
    sweep, prior_low = lows[-2], lows[-3]
    if not sweep.price < prior_low.price:
        return None
    if not higher_low.price > sweep.price:
        return None

    # 2. CHoCH — a swing high between the sweep and the higher low, which
    #    broke the last swing high standing before the sweep.
    before_sweep = [h for h in highs if h.index < sweep.index]
    between = [h for h in highs if sweep.index < h.index < higher_low.index]
    if not before_sweep or not between:
        return None
    choch = max(between, key=lambda h: h.price)
    if not choch.price > before_sweep[-1].price:
        return None

    # 4. BOS — this bar takes out the CHoCH high. The trigger.
    if not bars.highs[index] > choch.price:
        return None

    entry = bars.closes[index]
    stop = higher_low.price
    # TARGET — the range high the sweep was taken from.
    target = max(h.price for h in before_sweep)

    if not stop < entry < target:
        return None
    setup = Setup(
        symbol=bars.symbol,
        trigger_index=index,
        sweep_index=sweep.index,
        sweep_price=sweep.price,
        choch_index=choch.index,
        choch_price=choch.price,
        higher_low_index=higher_low.index,
        higher_low_price=higher_low.price,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
    )
    if setup.reward_risk < min_reward_risk:
        return None
    if setup.entry_price > 0 and setup.risk / setup.entry_price * 100.0 < min_risk_pct:
        return None  # too tight to survive friction
    return setup


def priority(setup: "Setup", *, rs_percentile: float | None = None) -> float:
    """Rank one setup against another. Higher sorts first.

    Candidates were previously ordered by trigger time — most recent first —
    which is not a ranking, it is an accident of iteration. When five setups
    fire and two slots are free, something has to choose, and recency is a
    worse basis than anything measured.

    The weights are deliberately crude and readable rather than fitted. Each
    component is something the holdout study actually supported:

      stop width      wider stops were better setups AND cheaper to trade;
                      this is the strongest single finding in the study
      reward:risk     room to run, capped so a tight stop cannot buy rank
      relative strength  optional; promising out-of-sample but chosen from
                      five variants, so it is weighted lightly

    No fitting was done on this score. It exists so the choice is explainable,
    not to squeeze the edge — and it is deterministic, so it stays testable.
    """
    if setup.entry_price <= 0:
        return 0.0
    stop_pct = setup.risk / setup.entry_price * 100.0
    score = min(stop_pct, 5.0) * 10.0            # wider stop, better setup
    score += min(setup.reward_risk, 6.0) * 3.0   # room to run, capped
    if rs_percentile is not None:
        score += rs_percentile * 0.10            # light touch, lightly evidenced
    return score


def swept_value_area(
    bars: IntradayBars,
    setup: Setup,
    *,
    bins: int = profile.DEFAULT_BINS,
    value_area: float = profile.DEFAULT_VALUE_AREA,
    window: int = DEFAULT_LOOKBACK,
) -> bool:
    """Did the sweep drop below value and the entry reclaim it?

    The volume profile turns "a range" into "where trade actually
    concentrated". The claim this tests is narrower and more falsifiable than
    the raw pattern: price dipped under the value area low, found nothing,
    and came back inside. A sweep that never left value swept nothing.

    Returns False rather than raising when the range cannot be profiled — an
    unprofilable range is a filter that did not pass, not an error.
    """
    try:
        built = profile.build(
            bars,
            setup.sweep_index - window,
            setup.trigger_index,
            bins=bins,
            value_area=value_area,
        )
    except profile.ProfileError:
        return False
    return setup.sweep_price < built.value_area_low <= setup.entry_price


def scan(
    bars: IntradayBars,
    *,
    k: int = DEFAULT_K,
    lookback: int = DEFAULT_LOOKBACK,
    min_reward_risk: float = MIN_REWARD_RISK,
    min_risk_pct: float = 0.0,
    require_value_sweep: bool = False,
    require_trend_context: bool = False,
    require_rsi_context: bool = False,
    require_macd_context: bool = False,
) -> tuple[Setup, ...]:
    """Every trigger across the series, one per pattern instance.

    A pattern stays true for as long as price holds above the CHoCH high, so
    the naive scan re-fires it on every later bar. Only the first trigger for
    a given (sweep, higher low) pair is a trade; the rest are the same idea
    counted again, which is how a backtest quietly multiplies its sample.
    """
    found: list[Setup] = []
    seen: set[tuple[int, int]] = set()
    points = swings(bars.highs, bars.lows, k)  # once per series, not per bar
    for index in range(len(bars)):
        setup = detect(
            bars, index, k=k, lookback=lookback,
            min_reward_risk=min_reward_risk, min_risk_pct=min_risk_pct,
            points=points,
        )
        if setup is None:
            continue
        key = (setup.sweep_index, setup.higher_low_index)
        if key in seen:
            continue
        seen.add(key)
        if require_value_sweep and not swept_value_area(bars, setup, window=lookback):
            continue
        context_bars = bars.upto(index)
        if not intraday_context.passes(
            context_bars,
            require_trend=require_trend_context,
            require_rsi=require_rsi_context,
            require_macd=require_macd_context,
        ):
            continue
        found.append(setup)
    return tuple(found)
