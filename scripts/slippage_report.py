"""What execution has actually cost, in R.

    python scripts/slippage_report.py

READ-ONLY. Touches no order and no policy. Safe to run during the frozen
evaluation window.

WHY THIS REPLACES A NUMBER THAT WAS ALREADY THERE
`positions.py` reports "mean stop fill +1.4955 vs the stop level" — dollars per
share, averaged over trades whose stop distances differ by an order of
magnitude. AMD's stop sat $8.51 below its entry, CCL's $0.61. Averaging a
dollar gap across those says nothing, and the figure was nonetheless quoted as
evidence that paper fills were optimistic.

In R the same fills read +0.354R and -0.033R, and the claim becomes checkable:
a stop filling a third of an R better than its level is the simulator being
generous, and against a +0.218R expectancy that is not a rounding detail.

WHAT IT REFUSES TO DO
Quote a mean from a sample too small to have one. Two stopped trades cannot
support a distribution, and slicing them by liquidity, volatility, time of day
and outcome produces cells of size one. The report says so rather than
printing a confident number.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.reconcile import CLOSED_STOP, CLOSED_TARGET, Summary, reconcile
from scanner.execution.slippage import MIN_REPORTABLE, FillRecord, summarise

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

_ROOT = Path(__file__).resolve().parent


def _load_intents():
    """`scripts/positions.py` owns the audit reader and is not a package."""
    spec = importlib.util.spec_from_file_location(
        "_positions", _ROOT / "positions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_positions"] = module
    spec.loader.exec_module(module)
    return module.load_intents(settings.audit_log_path)


def _records(trades) -> list[FillRecord]:
    out: list[FillRecord] = []
    for trade in trades:
        risk = trade.intended_entry - trade.intended_stop
        if trade.entry_fill is not None:
            out.append(FillRecord(
                symbol=trade.symbol, kind="entry", side="buy",
                intended=trade.intended_entry, actual=trade.entry_fill,
                risk_per_share=risk))
        if trade.exit_fill is None:
            continue
        if trade.state == CLOSED_STOP:
            out.append(FillRecord(
                symbol=trade.symbol, kind="stop", side="sell",
                intended=trade.intended_stop, actual=trade.exit_fill,
                risk_per_share=risk))
        elif trade.state == CLOSED_TARGET:
            out.append(FillRecord(
                symbol=trade.symbol, kind="target", side="sell",
                intended=trade.intended_target, actual=trade.exit_fill,
                risk_per_share=risk))
    return out


def _report(label: str, records: list[FillRecord]) -> None:
    summary = summarise(records)
    if not summary.count:
        print(f"  {label:<10}{DIM}no fills yet{RESET}")
        return
    mark = "" if summary.is_reportable else f"  {YELLOW}<- n < {MIN_REPORTABLE}{RESET}"
    tint = RED if (summary.mean_adverse_r or 0) > 0 else GREEN
    print(f"  {label:<10}n={summary.count:<4} "
          f"mean {tint}{summary.mean_adverse_r:+.4f}R{RESET}  "
          f"median {summary.median_adverse_r:+.4f}R  "
          f"worst {summary.worst_adverse_r:+.4f}R  "
          f"favourable {summary.favourable}/{summary.count}"
          f" ({summary.favourable_share:.0f}%){mark}")


async def main() -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        # `nested=true` is what associates bracket legs with their parent;
        # without it the exit fills are invisible and every trade reads as
        # still open. positions.py has always fetched this way.
        orders = await broker._get("/v2/orders", status="all",
                                   nested="true", limit=500,
                                   direction="desc")

    summary = Summary(trades=reconcile(_load_intents(), orders))
    records = _records(summary.ours)
    if not records:
        print(f"{YELLOW}no tracked fills yet{RESET}")
        return 0

    print(f"{BOLD}EXECUTION COST{RESET} {DIM}(positive R = money lost to "
          f"execution){RESET}\n")
    for kind in ("entry", "stop", "target"):
        _report(kind, [r for r in records if r.kind == kind])
    print()
    _report("all", records)

    print(f"\n{BOLD}PER FILL{RESET}")
    print(f"  {'sym':<6}{'kind':<8}{'intended':>10}{'actual':>10}"
          f"{'risk/sh':>9}{'adverse R':>11}")
    for r in records:
        value = r.adverse_r
        tint = RED if r.is_adverse else (GREEN if r.is_favourable else DIM)
        shown = f"{value:+.4f}" if value is not None else "n/a"
        print(f"  {r.symbol:<6}{r.kind:<8}{r.intended:>10.2f}{r.actual:>10.2f}"
              f"{r.risk_per_share:>9.2f}{tint}{shown:>11}{RESET}")

    stops = summarise([r for r in records if r.kind == "stop"])
    if stops.count and stops.favourable:
        print(f"\n{YELLOW}{stops.favourable} of {stops.count} stops filled BETTER "
              f"than their level.{RESET}")
        print(f"{DIM}  A live stop becomes a market order the instant it triggers "
              f"and fills at{RESET}")
        print(f"{DIM}  or worse than the level, never above it. Every favourable "
              f"stop here is the{RESET}")
        print(f"{DIM}  simulator being generous, and it flatters this account "
              f"against both the{RESET}")
        print(f"{DIM}  backtest and a funded one.{RESET}")

    if not summarise(records).is_reportable:
        print(f"\n{YELLOW}Sample too small to model.{RESET}")
        print(f"{DIM}  Fewer than {MIN_REPORTABLE} fills. Slicing these by "
              f"liquidity, volatility, time of{RESET}")
        print(f"{DIM}  day and outcome would produce cells of size one. Collect "
              f"through the frozen{RESET}")
        print(f"{DIM}  window first; until then assume stops fill AT the level "
              f"or worse, never{RESET}")
        print(f"{DIM}  above it — a conservative assumption beats an estimated "
              f"one at this n.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
