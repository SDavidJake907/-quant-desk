"""Do the shadow dimensions predict anything?

    python scripts/shadow_test.py
    python scripts/shadow_test.py --index all --draws 50000

READ-ONLY research. It resolves historical setups from the accumulated store
and reports, nothing more.

WHAT THE SHADOW FLAGS ARE
`TradingPolicy` carries four `shadow_*` fields — RSI gate, session slot,
weekday, LLM ranking — described as "recorded, never enforced… turning one on
is a deliberate act". Each looked good on 86 days of data and none had forward
evidence. Sector and liquidity tier are recorded the same way.

THEY ARE BEING MEASURED AT THE WRONG LAYER
They are written onto DECISIONS, of which there are 99, and `rsi_gate_passed`
is True on 97 of them. Comparing a 98% group against a 2% group needs
thousands of trades; at 0.3 trades a day that is never. But every one of these
is arithmetic over bars already held, so they can be computed for hundreds of
historical SETUPS today instead of waiting years for live decisions.

This does that. LLM ranking is excluded — it reads headlines, and headlines
were not persisted before 2026-09-25, so it is the one shadow dimension that
genuinely cannot be answered from history.

FIVE DIMENSIONS MEANS FIVE CHANCES TO BE FOOLED
This project has already paid for that lesson once: thirteen ablation
variants, best-of selection, +0.429R on train and +0.014R on holdout. Testing
several dimensions at once is the same shape of mistake wearing different
clothes, so:

  - the split to test is chosen on TRAIN and then applied unchanged to
    HOLDOUT, never picked from the holdout column;
  - a Bonferroni threshold is printed beside the raw p, because a p of 0.04
    across five independent questions is not a finding;
  - every group carries its n, and small groups are called out rather than
    ranked.

The expected result, written before running: nothing survives. Seven of nine
context gates made the holdout worse when this was last tried, and these are
the survivors of that same cull.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import statistics
from collections import defaultdict

import httpx

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history, intraday_context
from scanner.features.intraday import SESSION_ALIGNED, session_slot
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

VALIDATED = TradingPolicy()

#: Dimensions tested together, so the multiple-comparison correction is
#: honest about how many questions were asked.
DIMENSIONS = ("rsi_gate", "session_slot", "weekday", "liquidity_tier")


def _liquidity_tier(dollar_volume: float) -> str:
    if dollar_volume >= 50_000_000:
        return "over-50M"
    if dollar_volume >= 20_000_000:
        return "20-50M"
    return "5-20M"


def label_setup(bars, setup) -> dict[str, str]:
    """Every shadow dimension, computed as of the TRIGGER bar.

    `bars.upto` matters: a label computed from the whole series would use
    prices that had not printed when the decision was made, which is the
    lookahead this project has already been bitten by.
    """
    upto = bars.upto(setup.trigger_index)
    at = bars.times[setup.trigger_index]
    window = bars.closes[max(0, setup.trigger_index - 19):setup.trigger_index + 1]
    volumes = bars.volumes[max(0, setup.trigger_index - 19):setup.trigger_index + 1]
    dollar_volume = (sum(v * c for v, c in zip(volumes, window)) / len(window)
                     if window else 0.0)
    return {
        "rsi_gate": str(intraday_context.passes(upto, require_rsi=True)),
        "session_slot": session_slot(at),
        "weekday": at.strftime("%a"),
        "liquidity_tier": _liquidity_tier(dollar_volume),
    }


def permutation_p(a: list[float], b: list[float], draws: int) -> float:
    """Two-group randomisation on the difference of means."""
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool = a + b
    cut = len(a)
    rng = random.Random(0)          # fixed seed: the answer must not wander
    hits = 0
    for _ in range(draws):
        rng.shuffle(pool)
        gap = abs(statistics.fmean(pool[:cut]) - statistics.fmean(pool[cut:]))
        if gap >= observed:
            hits += 1
    return hits / draws


def _groups(rows, field) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for labels, r in rows:
        out[labels[field]].append(r)
    return out


def report(name: str, rows, field: str, min_group: int) -> tuple[str, str] | None:
    groups = _groups(rows, field)
    usable = {k: v for k, v in groups.items() if len(v) >= min_group}
    print(f"\n  {BOLD}{field}{RESET} {DIM}({name}){RESET}")
    if len(usable) < 2:
        print(f"    {YELLOW}fewer than two groups reach n>={min_group} — "
              f"undecidable{RESET}")
        return None
    ordered = sorted(usable.items(), key=lambda kv: -statistics.fmean(kv[1]))
    for value, values in ordered:
        mean = statistics.fmean(values)
        se = (statistics.stdev(values) / math.sqrt(len(values))
              if len(values) > 1 else 0.0)
        tint = GREEN if mean > 0 else RED
        print(f"    {value:<12}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
              f"n={len(values)}")
    return ordered[0][0], ordered[-1][0]


async def main(index: str, minutes: int, holdout: float, draws: int,
               min_group: int, limit: int) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
    symbols = [m.symbol for m in universe]
    if limit:
        symbols = symbols[:limit]

    found = []
    for symbol in symbols:
        bars = history.accumulated(symbol, minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=VALIDATED.swing_k,
                          require_value_sweep=VALIDATED.require_value_sweep,
                          min_risk_pct=VALIDATED.min_risk_pct,
                          min_reward_risk=VALIDATED.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found.append((bars, setup))
    if not found:
        raise SystemExit("no setups — is the store populated?")

    found.sort(key=lambda row: row[1].trigger_index / len(row[0]))
    rows = []
    for bars, setup in found:
        outcome = resolve(
            bars, symbol=setup.symbol, entry_index=setup.trigger_index,
            entry_price=setup.entry_price, stop_price=setup.stop_price,
            target_price=setup.target_price, max_bars=VALIDATED.max_hold_bars,
        )
        if outcome is None:
            continue
        rows.append((label_setup(bars, setup), outcome.r_multiple))

    cut = int(len(rows) * (1 - holdout))
    train, hold = rows[:cut], rows[cut:]
    print(f"{DIM}{len(rows)} resolved setups from the store; "
          f"train {len(train)}, holdout {len(hold)}{RESET}")
    print(f"{DIM}overall expectancy "
          f"{statistics.fmean([r for _, r in rows]):+.3f}R{RESET}")

    threshold = 0.05 / len(DIMENSIONS)
    print(f"\n{BOLD}TRAIN — choose the split here{RESET}")
    chosen: dict[str, tuple[str, str]] = {}
    for field in DIMENSIONS:
        picked = report("train", train, field, min_group)
        if picked:
            chosen[field] = picked

    print(f"\n{BOLD}HOLDOUT — the same split, applied unchanged{RESET}")
    for field in DIMENSIONS:
        report("holdout", hold, field, min_group)

    print(f"\n{BOLD}DOES ANY SPLIT SURVIVE?{RESET}")
    print(f"{DIM}  best vs worst group chosen on TRAIN, tested on HOLDOUT."
          f" {len(DIMENSIONS)} questions asked, so the honest threshold is"
          f" 0.05/{len(DIMENSIONS)} = {threshold:.3f}{RESET}")
    for field, (best, worst) in chosen.items():
        groups = _groups(hold, field)
        a, b = groups.get(best, []), groups.get(worst, [])
        if len(a) < min_group or len(b) < min_group:
            print(f"    {field:<16}{YELLOW}holdout groups too small"
                  f" ({len(a)} vs {len(b)}){RESET}")
            continue
        gap = statistics.fmean(a) - statistics.fmean(b)
        p = permutation_p(list(a), list(b), draws)
        if p < threshold:
            verdict = f"{GREEN}SURVIVES{RESET}"
        elif p < 0.05:
            verdict = f"{YELLOW}p<0.05 but not after correction{RESET}"
        else:
            verdict = f"{DIM}nothing{RESET}"
        print(f"    {field:<16}{best} vs {worst}: {gap:+.3f}R  "
              f"n={len(a)}/{len(b)}  p={p:.3f}  {verdict}")

    print(f"\n{DIM}A dimension that does not survive is not a dimension that "
          f"is useless — it is one this sample cannot resolve. Recording it "
          f"costs nothing; acting on it needs evidence that is not here."
          f"{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes)
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--draws", type=int, default=20_000)
    p.add_argument("--min-group", type=int, default=10)
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args()
    asyncio.run(main(a.index, a.minutes, a.holdout, a.draws,
                     a.min_group, a.limit))
