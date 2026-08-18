"""The rules, in one place, versioned and attached to every decision.

WHY A POLICY OBJECT RATHER THAN SCATTERED FLAGS
The rules that decide a trade currently live in six places: constants in the
screen, defaults in argparse, thresholds in the guards, a liquidity floor in
the runner. That is fine while one person runs one command by hand. It stops
being fine the moment trades accumulate into evidence, because six months from
now the only question that matters about a losing month is "what rules were
running", and nothing today can answer it.

So: one frozen object, hashed, written beside every decision. A result can
always be traced back to the exact rules that produced it.

THE FOUR STAGES
    SCAN     the deterministic detector found a setup
    WATCH    it is a real candidate, ranked against its peers
    CONFIRM  the gates ran — liquidity, news, earnings, risk, duplicates
    TRADE    an order went to the broker

Every candidate reaches exactly one final stage and records why it stopped.
Rejections are data: "the screen found 40 setups and refused 38, here is the
breakdown by rule" is a far more useful record than two orders and silence.

WHAT THIS IS NOT
It is not a new entry rule. Entry is still the close of the trigger bar, which
is what the holdout measured. Adding a confirmation *delay* — wait for the next
bar, wait for a retest — would be a different strategy and would need its own
holdout before it could claim the same evidence.
"""

from __future__ import annotations

import hashlib
import json

from scanner.models import Frozen

SCAN = "scan"
WATCH = "watch"
CONFIRM = "confirm"
TRADE = "trade"
STAGES = (SCAN, WATCH, CONFIRM, TRADE)


class TradingPolicy(Frozen):
    """Every rule that can stop a trade. Hashed into the audit record."""

    # --- what counts as a setup ------------------------------------------
    #
    # THESE FOUR ARE THE VALIDATED CONFIGURATION. The +0.218R out-of-sample
    # result was measured at 30m, k=2, value sweep on, 1.5% stop floor. Any
    # entry point whose defaults differ is trading or testing something the
    # evidence does not cover — which is exactly what happened before this
    # object existed: the backtest defaulted to 4h with NO stop floor and the
    # live runner defaulted to 4h, while every result quoted came from 30m.
    # `test_policy.py` pins these so a default cannot drift again.
    interval_minutes: int = 30
    swing_k: int = 2
    require_value_sweep: bool = True
    min_risk_pct: float = 1.5
    min_reward_risk: float = 1.5
    #: How many bars back a trigger may be and still be acted on. TWO, not
    #: four, because the measured entry is the CLOSE OF THE TRIGGER BAR — every
    #: bar of delay is slippage away from the thing the holdout measured, and
    #: on 30m bars four is a two-hour-old price. `trade.py` already used 2
    #: while this object claimed 4 and `find_setups.py` claimed 4; aligning on
    #: the tighter value leaves live behaviour unchanged and makes the
    #: declaration honest.
    fresh_bars: int = 2

    # --- what is tradeable ------------------------------------------------
    min_price: float = 5.0
    min_dollar_volume: float = 5_000_000.0

    # --- what stops a trade -----------------------------------------------
    block_structural_news: bool = True
    block_earnings_within_days: int = 7

    #: Refuse a long whose stop would be consumed by the mechanical
    #: ex-dividend price drop. THIS IS NOT A PREDICTIVE FILTER. Every context
    #: gate rejected in this project claimed that some condition predicts
    #: better outcomes, and seven of nine made the holdout worse. This claims
    #: nothing about the future: on the ex-date the exchange reduces the open
    #: by the dividend, and MO's is 1.56% against a 1.5% minimum stop. It is a
    #: correction to a modelling error, not an optimisation — the backtest ran
    #: on Yahoo's ADJUSTED bars, where the drop has been smoothed away, so it
    #: measured a world in which this cost does not exist.
    block_ex_dividend_erosion: bool = True

    #: Share of the stop the drop may consume before a setup is refused.
    ex_dividend_stop_threshold: float = 0.33

    # --- how big -----------------------------------------------------------
    #: Share of the account this strategy may draw on. The rest is the income
    #: sleeve: held continuously, never sized or sold by this code. Sizing runs
    #: against the sleeve, not the balance — otherwise "1% of equity" quietly
    #: becomes 1.5% of the money the strategy actually has. 100 means the whole
    #: account, which is how this behaved before sleeves existed.
    swing_allocation_pct: float = 100.0

    #: Hard ceiling on the swing sleeve in dollars, or None for no ceiling.
    #: A percentage floats with the account, so profit stays where it was made;
    #: a cap holds the strategy at a fixed size and lets everything above it
    #: fall to the income sleeve. Only the cap makes income grow faster than
    #: the account, which is what "reinvest profits into dividends" means.
    swing_cap: float | None = None

    risk_pct: float = 1.0
    max_position_pct: float = 20.0
    max_positions: int = 5
    max_daily_loss_pct: float = 3.0
    min_equity: float = 1_000.0

    # --- when the market is shut --------------------------------------------
    #: Refuse a setup whose trigger bar ended a session.
    #:
    #: NOT A PREDICTIVE FILTER, and adopted on the same grounds as the
    #: ex-dividend gate: it corrects a modelling error rather than claiming an
    #: edge. `fresh_bars` counts BARS, so overnight is one bar and seventeen
    #: hours, and a setup from yesterday afternoon reads as fresh three minutes
    #: after today's open. The backtest measured entry at the CLOSE OF THE
    #: TRIGGER BAR; across a session boundary that price was never available,
    #: because the market was shut. Measured on 60 days of 30m bars: entries
    #: across the close are adverse 88% of the time, mean +0.785%, worst
    #: +3.20% -- more than twice the 1.5% minimum stop consumed before the
    #: position exists. trade.py already refuses these while the market is
    #: closed; this extends the same refusal three minutes past the open.
    block_overnight_entry: bool = True

    #: Close every position at the last bar before the weekend.
    #:
    #: OFF, AND EXPENSIVE. This one is not a correction, it is a risk purchase
    #: with a measured price: -0.213R per trade, taking expectancy from +0.447R
    #: to +0.233R against a validated holdout of +0.218R. It consumes
    #: essentially the whole edge, because max_hold_bars of 60 means 36% of
    #: setups span a weekend. Weekend gaps are ALREADY modelled -- a bar that
    #: opens through the stop fills at the open -- so this does not fix an
    #: unpriced risk; it removes an exposure no stop can cover, at that price.
    #: Turning it on is a deliberate choice, and it has been made: the
    #: operator judged an unmanageable weekend exposure worse than the cost.
    flat_before_weekend: bool = True

    #: Share of equity that may be committed to NEW positions, by weekday
    #: (Monday first). Falls toward the weekend and reaches zero where the
    #: wind-down already refuses entries, so the two rules cannot disagree.
    #:
    #: NOT A WEEKDAY EDGE. Expectancy by trigger day measured Mon +0.539R,
    #: Tue +0.054R, Wed -0.562R, Thu +0.780R, Fri +1.068R over 125 setups, and
    #: a permutation test puts the chance of a random five-way split producing
    #: a best day at least that good at 18.9%. Train and holdout disagree about
    #: which day wins. This is a risk schedule, and since exposure and return
    #: scale together here it costs return roughly in proportion.
    weekday_deployment_pct: tuple[float, float, float, float, float] = (
        50.0, 40.0, 30.0, 0.0, 0.0)

    #: Weekday from which no NEW positions are opened, Monday being 0.
    #: Thursday, because flattening on Friday alone still allows a position to
    #: be opened on Friday morning with hours left to go wrong. Existing
    #: positions keep their brackets and are closed by scripts/flatten.py.
    wind_down_from_weekday: int = 3

    # --- how it ends --------------------------------------------------------
    max_hold_bars: int = 60

    # --- recorded, never enforced ------------------------------------------
    #: Each of these improved the holdout and none has forward evidence. They
    #: are logged on every decision so the question can be settled later on
    #: data nothing was chosen from. Turning one on is a deliberate act.
    shadow_rsi_gate: bool = True
    shadow_session_slot: bool = True
    shadow_weekday: bool = True
    shadow_llm_ranking: bool = True

    @property
    def fingerprint(self) -> str:
        """Short stable hash. Two runs with the same fingerprint ran the same
        rules; a change in results across different fingerprints is a change
        in policy, not in the market."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def describe(self) -> str:
        return (f"{self.interval_minutes}m k={self.swing_k} "
                f"stop>={self.min_risk_pct}% rr>={self.min_reward_risk} "
                f"risk={self.risk_pct}% max={self.max_positions} "
                f"[{self.fingerprint}]")


class Decision(Frozen):
    """One candidate's journey, and where it stopped."""

    symbol: str
    stage: str
    reason: str = ""
    priority_rank: int = 0

    @property
    def traded(self) -> bool:
        return self.stage == TRADE


def summarise(decisions: tuple[Decision, ...]) -> dict[str, int]:
    """Counts by final stage — the shape of the funnel for one run."""
    counts = {stage: 0 for stage in STAGES}
    for decision in decisions:
        counts[decision.stage] = counts.get(decision.stage, 0) + 1
    return counts


def rejections(decisions: tuple[Decision, ...]) -> dict[str, int]:
    """Why candidates were refused, most common first.

    This is the number that tells you whether a quiet day means a quiet market
    or a rule that has quietly stopped letting anything through.
    """
    counts: dict[str, int] = {}
    for decision in decisions:
        if decision.traded or not decision.reason:
            continue
        counts[decision.reason] = counts.get(decision.reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
