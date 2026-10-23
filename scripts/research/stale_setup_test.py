"""Is a setup still worth taking two hours after it triggered?

    python scripts/stale_setup_test.py --index sp500
    python scripts/stale_setup_test.py --delays 0 1 2 3 5 8 13

THE QUESTION THIS SETTLES, FROM WORKLIST section 8
`fresh_bars = 2` rejects 8,455 of 8,479 setups the live scan has ever seen —
99.7%, against 13 for every other gate combined. Eleven candidates have
survived in 114 scans. At that rate the evaluation window cannot reach the
sample it was opened to collect, so this is the parameter that decides whether
this strategy produces evidence at all.

The temptation is obvious and is exactly what this project has already paid
for once: widen the gate, get more trades, discover later that the extra
trades were worse. So the question is not "can we have more" but "do older
setups still work" — and it is answerable now, on bars already held, without
risking anything.

HOW A DELAY IS SIMULATED
Enter `delay` bars after the trigger, at THAT bar's close, keeping the stop
and target the setup was built with. That is what the live scanner would
actually do: the levels come from the structure, and only the entry moves.

The consequence is the honest cost. Risk is entry minus stop, so a later entry
into a move that has already run means a wider risk for the same target — the
same move earns less R. Nothing here has to model slippage separately; it
falls out of the arithmetic.

A delay that has already run through the stop or the target produces no trade
at all, because `resolve` refuses an entry that is not between them. Those
show up as the surviving count falling, which is the other half of section 8's
question: how many setups are even still takeable N bars later.

DISCIPLINE
This is a parameter search over a strategy with a validated number, which is
the shape of every idea that died in this repo. So the winner is read from
TRAIN alone and HOLDOUT is printed for every delay, not to choose from but to
show whether a whole surface moved or one cell got lucky. Reading the holdout
column to pick a value would make it a training set, and there is no second
one.

Expected result, stated before running so it cannot be rationalised after:
expectancy should DECAY with delay. The strategy enters at the close of the
trigger bar because that is where it was measured. If instead it is flat
across delays, the freshness gate is costing evidence for nothing and should
be widened. If it decays, scarcity is the honest output of this strategy and
`fresh_bars` should be left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import math

import httpx

from scanner.backtest.setups import STOP, TARGET, TIMEOUT, Report, resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import (
    SESSION_ALIGNED, fetch_intraday, max_range,
)
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()
SESSION_BARS = 13


def _delayed(bars, setup, delay: int, max_bars: int):
    """Resolve the setup as if entered `delay` bars after its trigger.

    None when the delayed entry is no longer a trade: price already through
    the stop or the target, or the series ends first. That is not a failure to
    measure — it is the setup having expired, and counting it as anything else
    would flatter the delay.
    """
    index = setup.trigger_index + delay
    if index >= len(bars) - 1:
        return None
    entry = bars.closes[index]

    # RE-APPLY THE POLICY AT THE PRICE ACTUALLY PAID.
    #
    # `scan` enforces min_risk_pct and min_reward_risk at the TRIGGER price.
    # Delaying the entry moves the price and leaves the stop where it was, so
    # both can quietly stop holding — and the first version of this test did
    # not re-check them.
    #
    # The damage is not subtle. As a delayed entry drifts toward the stop the
    # risk denominator shrinks toward zero and R explodes: LITE resolved at
    # -135R on a $0.33 risk against an $866 stock, and HOOD at +82R on $0.07.
    # Those are not trades that went badly or well, they are trades the live
    # scanner would REFUSE, and averaging them measured a strategy nobody
    # runs. A single one of them moved a holdout column by more than a full R.
    risk = entry - setup.stop_price
    if risk <= 0:
        return None
    if (risk / entry) * 100.0 < VALIDATED.min_risk_pct:
        return None
    if (setup.target_price - entry) / risk < VALIDATED.min_reward_risk:
        return None

    return resolve(
        bars,
        symbol=setup.symbol,
        entry_index=index,
        entry_price=entry,
        stop_price=setup.stop_price,
        target_price=setup.target_price,
        max_bars=max_bars,
    )


def _line(label: str, outcomes: tuple, offered: int) -> None:
    report = Report(label=label, outcomes=outcomes)
    survived = f"{100 * report.count / offered:.0f}%" if offered else "-"
    if report.count < 5:
        print(f"    {label:<10}{report.count:>6}  {YELLOW}too few{RESET}")
        return
    se = report.stdev_r / math.sqrt(report.count)
    tint = GREEN if report.expectancy > 0 else RED
    print(f"    {label:<10}{report.count:>6}  {tint}{report.expectancy:>+7.3f}R{RESET}"
          f"  se {se:>5.3f}  win {report.win_rate:>5.1f}%"
          f"  {DIM}target {report.share(TARGET):4.1f}% stop {report.share(STOP):4.1f}%"
          f" timeout {report.share(TIMEOUT):4.1f}%{RESET}"
          f"  {BOLD}takeable {survived:>4}{RESET}")


def paired_decay(part, fresh: int, stale: int, max_bars: int,
                 draws: int = 20_000) -> tuple[int, float, float]:
    """Is the same setup worth less when taken later? Paired randomisation.

    THE PAIRING MATTERS. Every delay column holds the SAME setups resolved at
    different entry points, so the columns are not independent samples and a
    two-group test would overstate its confidence — the same mistake as the
    clustered t of +2.42 that WORKLIST section 5 flags on the momentum work.

    Each setup contributes one difference, R(fresh) - R(stale). Under the null
    that delay carries no information, the sign of each difference is
    arbitrary, so the null distribution is built by flipping signs at random
    rather than by shuffling values between groups.

    Returns (pairs, mean difference, two-sided p).
    """
    import random

    diffs = []
    for bars, setup in part:
        a = _delayed(bars, setup, fresh, max_bars)
        b = _delayed(bars, setup, stale, max_bars)
        if a is None or b is None:
            continue
        diffs.append(a.r_multiple - b.r_multiple)
    if len(diffs) < 5:
        return len(diffs), 0.0, 1.0

    observed = sum(diffs) / len(diffs)
    rng = random.Random(0)          # fixed seed: the answer must not wander
    hits = 0
    for _ in range(draws):
        total = sum(d if rng.random() < 0.5 else -d for d in diffs)
        if abs(total / len(diffs)) >= abs(observed):
            hits += 1
    return len(diffs), observed, hits / draws


async def main(index: str, minutes: int, k: int, delays: tuple[int, ...],
               holdout: float, limit: int) -> None:
    range_ = max_range(minutes)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        if limit:
            symbols = symbols[:limit]

        # PREFER THE ACCUMULATED STORE.
        #
        # Yahoo serves ~60 days of 30m bars on a rolling window; the store
        # holds every day it has ever been fed, currently about 96 sessions.
        # Re-fetching therefore throws away a third of the history that was
        # collected specifically so it would not be lost — "the days it
        # misses are gone for good" is the collector's own warning, and a
        # research script that re-fetches is ignoring it.
        #
        # `history.accumulated()` was written for exactly this and had no
        # callers. Fetch is the fallback for symbols never collected.
        frames = []
        from_store = fetched = 0
        missing = []
        for symbol in symbols:
            stored = history.accumulated(symbol, minutes, SESSION_ALIGNED)
            if stored is not None and len(stored) >= 60:
                frames.append(stored)
                from_store += 1
            else:
                missing.append(symbol)
        if missing:
            fresh = await asyncio.gather(
                *(fetch_intraday(s, client, minutes=minutes, range_=range_)
                  for s in missing),
                return_exceptions=True,
            )
            frames.extend(fresh)
            fetched = len(missing)

        depth = max((len(f) for f in frames
                     if not isinstance(f, Exception)), default=0)
        print(f"{DIM}{from_store} symbol(s) from the store, {fetched} fetched"
              f" — deepest series {depth} bars "
              f"(~{depth / SESSION_BARS:.0f} sessions){RESET}")

    print(f"{DIM}policy: {VALIDATED.describe()}{RESET}")
    print(f"{DIM}one session = {SESSION_BARS} bars; live gate is "
          f"fresh_bars={VALIDATED.fresh_bars}{RESET}")

    found = []
    for bars in frames:
        if isinstance(bars, Exception) or len(bars) < 60:
            continue
        for setup in scan(bars, k=k,
                          require_value_sweep=VALIDATED.require_value_sweep,
                          min_risk_pct=VALIDATED.min_risk_pct,
                          min_reward_risk=VALIDATED.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found.append((bars, setup))
    if not found:
        raise SystemExit("no setups")

    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    cut = int(len(found) * (1 - holdout))
    splits = (("TRAIN", found[:cut]), ("HOLDOUT", found[cut:]))
    print(f"{DIM}{len(found)} setups; train {cut}, "
          f"holdout {len(found) - cut}{RESET}")

    for name, part in splits:
        print(f"\n{BOLD}{name}{RESET}")
        print(f"    {DIM}{'delay':<10}{'n':>6}  expectancy   se      win"
              f"       outcomes                              takeable{RESET}")
        for delay in delays:
            outcomes = tuple(
                o for o in (
                    _delayed(bars, setup, delay, VALIDATED.max_hold_bars)
                    for bars, setup in part
                ) if o is not None
            )
            label = f"{delay} bar" + ("" if delay == 1 else "s")
            if delay <= VALIDATED.fresh_bars:
                label += " *"
            _line(label, outcomes, len(part))

    print(f"\n{BOLD}IS THE DECAY REAL?{RESET} {DIM}paired randomisation, "
          f"same setups fresh vs stale{RESET}")
    for name, part in splits:
        for stale in (8, 13, 20):
            pairs, mean, p = paired_decay(part, 0, stale,
                                          VALIDATED.max_hold_bars)
            if pairs < 5:
                continue
            verdict = (f"{GREEN}significant{RESET}" if p < 0.05
                       else f"{YELLOW}not significant{RESET}")
            print(f"    {name:<8} 0 vs {stale:>2} bars  "
                  f"n={pairs:<4} mean {mean:>+7.3f}R  p={p:.3f}  {verdict}")

    print(f"\n{DIM}* within the live freshness gate — these are the only "
          f"delays the scanner currently acts on.{RESET}")
    print(f"{DIM}Read TRAIN to decide and HOLDOUT only to see whether the "
          f"whole surface moved. A delay that keeps its expectancy is "
          f"evidence the gate is costing trades for nothing; decay is "
          f"evidence the scarcity is real and fresh_bars should be left "
          f"alone.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--delays", type=int, nargs="+",
                   default=[0, 1, 2, 3, 5, 8, 13, 20])
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--limit", type=int, default=0,
                   help="cap the universe, for a faster spot check")
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.k, tuple(a.delays),
                     a.holdout, a.limit))
