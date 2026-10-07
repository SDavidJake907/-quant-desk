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
    #: TURNED OFF 2026-09-28 after being measured against itself.
    #:
    #: Same scan, flag toggled, 30m Alpaca store:
    #:
    #:     True   n=860   +0.054R  se 0.058   total +46.3R
    #:     False  n=1468  +0.039R  se 0.044   total +57.1R
    #:
    #: The effect on per-trade quality is +0.015R against standard errors near
    #: 0.05 -- indistinguishable from zero. It does not measurably improve the
    #: trades it keeps. It discards 41% of the sample to do it, and total return
    #: is 23% HIGHER without it.
    #:
    #: Setup scarcity is the binding constraint on everything here: 224 trades
    #: are needed to exclude zero and the scan finds about 1.2 a session. This
    #: buys 70% more evidence per unit time at no measurable cost in quality.
    #:
    #: WHAT IS GIVEN UP. This encoded the strategy's own thesis, that a sweep
    #: should occur at a volume-profile value area, so removing it removes part
    #: of the idea rather than a parameter. And the difference sits inside the
    #: error bars, so the filter is not proven useless -- only unproven useful.
    #: Set back to True to restore it.
    require_value_sweep: bool = False
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
    #: A setup older than the latest bar is refused when the latest close is
    #: already this far BELOW its entry. fresh_bars admits a trigger one bar
    #: old at its old close as the limit; when the price has fallen since, the
    #: buy limit is above the market and fills at once beside the stop. Live,
    #: the only two fills more than 1% under the limit (APP 2026-08-20, PSX
    #: 09-24) were both orders sent one scan late, both 0.5% from the stop,
    #: both stopped within the hour; every on-time order filled within 0.34%.
    #: A genuine bug, fixed 2026-10-04 before window 15's first scan.
    max_entry_drift_pct: float = 0.5
    #: The entry limit sits this far ABOVE the trigger close. The order lands
    #: ~3.5 minutes after the bar closes and these setups bounce fast: a limit
    #: AT the close filled 91% of 538 setups replayed on 1-minute bars (live:
    #: 15 of 18 -- META, LULU, SLB touched and never filled) and the misses
    #: were net winners. +0.30% filled 99% and earned the most total R (+46.7R
    #: vs +34.5R), beating a market order while still refusing to pay for a
    #: spike. Together with max_entry_drift_pct: down 0.5% -> skip, up to 0.3%
    #: -> fill, beyond -> let it go. limit_minute_test.py, 2026-10-05.
    entry_limit_offset_pct: float = 0.3

    # --- what is tradeable ------------------------------------------------
    min_price: float = 5.0
    #: Liquidity floor, in dollars traded per bar.
    #:
    #: RAISED FROM 5M TO 20M ON MEASUREMENT. This strategy trades liquidity
    #: SWEEPS — price running through a level where stops are clustered. In a
    #: thinly traded name there are not enough resting orders for a sweep to
    #: mean anything, so the pattern matches noise instead of an event.
    #:
    #: Measured over 125 setups, split at the repo's own existing tier
    #: boundary rather than one chosen after looking:
    #:
    #:     below 20M   n=62   +0.114R   (train -0.152R, holdout +0.508R)
    #:     at/above    n=63   +0.776R   (train +0.673R, holdout +1.053R)
    #:
    #: Same direction and similar size on both splits, and a permutation test
    #: puts the chance of a gap that large from a random split at 2.2% — the
    #: strongest result this project has produced. Against 18.9% for the
    #: weekday claim and 25.1% for time of day, both of which were noise.
    #:
    #: The cost is real: it removes 263 of 499 symbols and halves the setup
    #: count, so evidence accumulates at half the rate. That is a deliberate
    #: trade of quantity for quality.
    min_dollar_volume: float = 20_000_000.0

    #: Average true bar range over the last 20 bars, as a % of price. A floor
    #: here says "this name moves enough for a 1.5% stop to be a level rather
    #: than a rounding error".
    #:
    #: ZERO — DISABLED — ON PURPOSE. This number existed as a hardcoded 0.4 in
    #: `find_setups.py` alone, where it silently narrowed the PREVIEW to 230
    #: symbols while `trade.py` acted on 267. The two disagreed, and the 37 in
    #: the gap were names like MA, KO, PG and BRK.B — WBD averages 0.088%, so a
    #: 1.5% stop there is seventeen bars of ordinary movement away.
    #:
    #: The argument for enabling it is the same MECHANISM argument that carried
    #: the 20M floor: this trades sweeps of resting orders, and a name that
    #: barely moves does not sweep. But the floor was measured before it was
    #: enforced (p=0.022 over 125 setups) and this has never been measured at
    #: all. Thirteen untested ranking ideas have failed here, several after
    #: looking obvious. So it ships DECLARED and OFF: both entry points now
    #: read the same number, changing it moves the fingerprint, and live
    #: behaviour is exactly what the holdout measured until a split says
    #: otherwise.
    min_range_pct: float = 0.0

    #: Minimum 12-1 relative strength against ^GSPC, in percentage points, for
    #: a setup to be tradeable. -10.0 excludes laggards and deep laggards.
    #:
    #: WHY IT IS HERE. Both positions opened 2026-09-28 were deep laggards:
    #: MLM at -29.2pp and CMG at -21.2pp, each below its 200-day average and
    #: down ~18% on the year. The screen has no opinion about trend, so a
    #: liquidity sweep in a name that has been sold for twelve months scores
    #: identically to one in a leader. The mechanism argument is real: a sweep
    #: is supposed to be a SHAKEOUT, which needs someone defending the level,
    #: and in a falling knife the break of lows may just be the selling
    #: continuing.
    #:
    #: IT TESTED NULL, AND THAT IS RECORDED RATHER THAN HIDDEN.
    #: `scripts/relative_strength_test.py` over 731 setups:
    #:
    #:     band            train            holdout
    #:     leader          -0.114R n=155    +0.596R n=104
    #:     in line         -0.248R n=108    +0.169R n=36
    #:     laggard         -0.134R n=83     -0.204R n=31
    #:     deep laggard    -0.044R n=141    +0.169R n=73
    #:
    #: Holdout favours leaders strongly, but TRAIN ranked deep laggards best
    #: and leaders third. The halves disagree, the chosen gap is +0.000R at
    #: p=0.999, and that is the signature of noise. Nineteen ideas have now
    #: failed the same bar.
    #:
    #: It is enabled anyway, as a deliberate and reversible choice by the
    #: operator, on the mechanism argument rather than the measurement. It is a
    #: declared field so it moves the fingerprint, and the cost is legible:
    #: laggard and deep-laggard bands are 45% of setups, so this roughly halves
    #: the trade rate in exchange for an edge the data does not confirm. Set it
    #: to -100.0 to disable.
    #: DISABLED 2026-09-28, the same day it was enabled.
    #:
    #: It was turned on against its own measurement, on the operator's
    #: judgement and a mechanism argument. The measurement has not improved:
    #: holdout favoured leaders (+0.596R n=104) while TRAIN ranked deep
    #: laggards best and leaders third, the chosen gap was +0.000R at p=0.999,
    #: and the halves disagreed -- the signature of noise.
    #:
    #: What settled it is the cost. It discards 45% of setups, and setup
    #: scarcity is the binding constraint on every open question here: 224
    #: trades are needed to exclude zero and the scan finds about 1.2 a
    #: session. A filter with no measured benefit that halves the evidence rate
    #: makes every other question slower to answer.
    #:
    #: -100.0 disables it. The field stays declared and fingerprinted so
    #: enabling it remains one number and stays visible in the audit trail.
    min_relative_strength: float = -100.0

    # --- what stops a trade -----------------------------------------------
    block_structural_news: bool = True
    #: 0 = hold through earnings (operator's choice for window 16, 2026-10-06:
    #: the core list is hand-picked trending names). Was 7.
    block_earnings_within_days: int = 0

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
    max_positions: int = 8
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
    #: DOUBLED 2026-09-25, deliberately, to make the dollars readable.
    #:
    #: At the old schedule every position was sized by the cap rather than by
    #: risk — `limited by position cap` printed on all three trades of the
    #: last window — and actual risk came out at 0.108% against a declared
    #: `risk_pct` of 1.0%. Nine times too small. Three trades at +0.44R
    #: produced $1,860 on a $100k account, which is not a result anyone can
    #: read a drawdown from.
    #:
    #: This changes NO expectancy. R is scale-invariant: doubling exposure
    #: doubles dollars and doubles drawdown and leaves +0.248R exactly where
    #: it was. What it buys is visibility — dollar P&L and drawdown large
    #: enough to mean something, and fills under a size that can actually
    #: move against you.
    #:
    #: The taper SHAPE is unchanged and still not a weekday edge. Monday
    #: still deploys most and Friday still deploys nothing, for the same
    #: bars-available arithmetic. Only the scale moved.
    #:
    #: Monday now hits `max_position_pct` exactly: min(20, 100/5) = 20%. So
    #: this is the most the existing ceilings allow without raising one, and
    #: going further is a separate decision about single-name concentration.
    #:
    #: EXPECT THE DAILY-LOSS GUARD TO START BINDING. It is inert at 0.108%
    #: risk per trade and will not stay inert at twice the size. A scan that
    #: refuses to trade after a bad morning is the guard working, not a fault.
    #: FLAT AT 100% from 2026-09-28, was (100, 80, 60, 40, 0)
    #: averaging 56% of the sleeve. `taper.py` always described the
    #: schedule as a PREFERENCE that costs return in proportion, with
    #: return-over-drawdown stable between 4.7 and 5.1 at every level
    #: tested. Raising it therefore raises return ~1.79x and drawdown
    #: ~1.79x, leaving that ratio where it was. It expresses whatever
    #: edge exists more strongly, including an edge of zero.
    weekday_deployment_pct: tuple[float, ...] = (
        100.0, 100.0, 100.0, 100.0, 100.0)

    #: Weekday from which no NEW positions are opened, Monday being 0.
    #: Thursday, because flattening on Friday alone still allows a position to
    #: be opened on Friday morning with hours left to go wrong. Existing
    #: positions keep their brackets and are closed by scripts/flatten.py.
    #: FRIDAY ONLY, on arithmetic rather than on a weekday edge.
    #:
    #: Measured bars available before the Friday flatten, against a ~20 bar
    #: mean hold:
    #:
    #:     Mon 63   Tue 50   Wed 37   Thu 17   Fri 11
    #:
    #: Mon-Wed have more room than the strategy needs, so the flatten costs
    #: them nothing. Thursday is just short — the flatten costs it -0.313R and
    #: it still lands at +0.467R, above the overall average, so it trades.
    #: Friday has about half the time it needs and the flatten takes it from
    #: +1.068R to +0.247R.
    #:
    #: The weekday EXPECTANCY differences are noise (18.9% on a permutation
    #: test, and train and holdout disagree about which day wins). Bars
    #: available is not — it is arithmetic, true every week whatever the
    #: sample size. This rule rests on the second thing, not the first.
    #: RAISED TO 5 (no wind-down) 2026-09-28, on the operator's decision and a
    #: measurement that removed the original justification.
    #:
    #: Friday entries were refused because the Friday flatten was priced at
    #: -0.213R per trade. `scripts/flatten_cost_test.py` measured it at
    #: **-0.012R, se 0.020** -- eighteen times smaller. It touches 26% of trades
    #: and those average +0.528R: they are profitable when closed, not
    #: sacrificed. The reason for excluding Friday was an estimate that did not
    #: survive measurement.
    #:
    #: And Friday is not a worse day. With the flatten modelled, by trigger day:
    #:
    #:     Mon -0.011R n=175   Tue -0.050R n=233   Wed +0.066R n=171
    #:     Thu +0.015R n=162   Fri -0.002R n=120  (highest win rate, 50.8%)
    #:
    #: Friday sits mid-pack and indistinguishable from Monday, while being 14%
    #: of all setups. Excluding it removed a seventh of the evidence for nothing
    #: measurable. Set back to 4 to restore the wind-down.
    wind_down_from_weekday: int = 5

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

    # --- what the model may decide ------------------------------------------
    #: Whether a grounded `veto` from the ranking model BLOCKS a trade rather
    #: than merely colouring it.
    #:
    #: Until now the model has been advisory in the strict sense: it ranks
    #: candidates the screen already chose and nothing it says can stop an
    #: order. That was the right default while its judgement was unmeasured.
    #:
    #: What IS measured: `scripts/gemma_audit.py` gives it the project's own
    #: facts and marks its answers against conclusions established earlier. It
    #: scores 2/2 on the gradeable questions, and unprompted it identified the
    #: outlier trade skewing the mean and the optimistic fill assumption --
    #: both of which were independently confirmed here.
    #:
    #: What is NOT measured: whether its per-candidate calls predict outcomes.
    #: That needs the slate to accumulate, and there are three slates.
    #:
    #: A veto is refused unless `grounded` is true, so an ungrounded opinion
    #: cannot block a trade, and every veto is recorded with its reason. If the
    #: model is unreachable nothing is vetoed -- the same fail-open direction
    #: the news gate already takes, because a dead model must not become a
    #: silent halt.
    #: DISABLED 2026-09-28 on measurement, hours after being enabled.
    #:
    #: `scripts/llm_compliance.py` asked the same input five times at
    #: temperature 0. On a pending all-cash acquisition -- the clearest case in
    #: its own prompt -- the model called it `weak` 5/5 and escalated to a veto
    #: 1/5. The judgement is stable; the action is a coin flip.
    #:
    #: A gate that fires on 20% of identical inputs is not a gate, and this one
    #: also UNDER-fires on the case it reliably recognises. Leaving it on means
    #: a live trade is blocked or not by which sample was drawn.
    #:
    #: The fix is not more prompt text. Derive the veto from `conviction ==
    #: weak` plus a named category, so it is deterministic given the judgement
    #: -- and the judgement IS stable. Until that exists this stays off, and
    #: the opinion continues to be recorded in the slate where it can be scored.
    llm_veto_enabled: bool = False

    @property
    def fingerprint(self) -> str:
        """Short stable hash. Two runs with the same fingerprint ran the same
        rules; a change in results across different fingerprints is a change
        in policy, not in the market."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def as_prose(self) -> str:
        """The live rules in English, GENERATED from the fields.

        WHY GENERATED AND NOT WRITTEN
        A model needs the rules in language, and the obvious way to give it
        them is to type them into the prompt. That creates a second copy of
        every rule, and the copy goes stale silently. It has already happened
        three times in this repository on one day: this module's own taper
        described a schedule that had changed; `ranking.py` claimed the model
        "never sees prices" after it had been given four of them; and the
        ranking prompt told the model price was not its job in the same breath
        as asking it to judge price.

        A human reading a stale docstring is mildly misled. A MODEL reading a
        stale prompt reasons confidently from a false premise and cannot check.

        So nothing here is typed. Every number and every clause comes from the
        field beside it, which means turning a rule off turns its sentence off
        too, and `test_policy.py` pins the correspondence.

        This describes the RULES only. The measured behaviour of the strategy
        -- how often it stops out, where the money comes from -- is evidence
        about it rather than policy, changes without the policy changing, and
        belongs in the caller's prompt, not here.
        """
        lines = [
            f"The screen looks for a liquidity sweep on {self.interval_minutes}"
            f"-minute bars: price takes out a prior swing low, makes a higher "
            f"low, breaks character, then breaks structure. Swings are "
            f"confirmed {self.swing_k} bars either side. It is LONG ONLY -- "
            f"there is no short side, so a bearish setup is invisible to it "
            f"rather than rejected.",
            f"Entry is the close of the break bar. The stop is the higher low, "
            f"and must sit at least {self.min_risk_pct}% below the entry. The "
            f"target is the highest swing high before the sweep, and must pay "
            f"at least {self.min_reward_risk} times the risk.",
            f"A name is only tradeable above ${self.min_price:.0f} and above "
            f"${self.min_dollar_volume / 1e6:.0f}M of trading per bar.",
        ]
        if self.min_range_pct > 0:
            lines.append(
                f"It must also average at least {self.min_range_pct}% of range "
                f"per bar.")
        if self.min_relative_strength > -100:
            lines.append(
                f"And it must not lag the S&P 500 by more than "
                f"{abs(self.min_relative_strength):.0f} points of 12-1 "
                f"momentum.")
        if self.require_value_sweep:
            lines.append("The sweep must occur at a volume-profile value area.")
        closes = [f"the stop or the target, held by the broker",
                  f"a limit of {self.max_hold_bars} bars in the position"]
        if self.flat_before_weekend:
            closes.append("a forced close before the weekend")
        lines.append("A trade ends at " + ", or ".join(closes) + ".")
        lines.append(
            f"At most {self.max_positions} positions are open at once, each "
            f"risking {self.risk_pct}% of equity in principle and capped at "
            f"{self.max_position_pct}% of equity in size -- the cap usually "
            f"binds, so real risk per trade is smaller than the headline.")
        if self.block_earnings_within_days:
            lines.append(
                f"A name reporting earnings within {self.block_earnings_within_days} "
                f"days is refused.")
        return '\n'.join(lines)

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
