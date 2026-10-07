"""Second opinions, kept apart from the decision (WORKLIST §38).

    python scripts/second_opinion.py MU AMD       # one page per symbol
    python scripts/second_opinion.py --grades     # the graded record only

THREE TIERS (advisor/opinions.py)
  PRIMARY             the momentum rule -- the one thing measured out of sample
  ADVISORY, UNPROVEN  TradingAgents, Gemma's news read: logged, graded against
                      the S&P over 21 sessions, shown with that record
  ADVISORY, NO EDGE   RSI, moving averages, chart patterns, the sweep: tested
                      here on thousands of events; shown for reference, labelled

There is no combined score and no vote: an unproven opinion does not outweigh
the rule with evidence. What IS shown is disagreement -- an advisory sell on a
momentum pick -- because that is the only call that could change a decision,
and it is scored on its own.

READ-ONLY. No order, no audit row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import grading, opinions as op
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday_context import rsi
from scanner.features.technicals import compute
from scanner.patterns import live as patterns

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"
BENCHMARK = "^GSPC"
RECORDS = (Path("data/momentum/paper.jsonl"), Path("data/momentum/dry_run.jsonl"))


def picks_by_day() -> dict[date, frozenset[str]]:
    """Every momentum rebalance on record, for the disagreement column."""
    out: dict[date, frozenset[str]] = {}
    for path in reversed(RECORDS):          # paper overrides dry run on a shared day
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[date.fromisoformat(rec["date"])] = frozenset(rec.get("picks") or ())
    return out


def _series(bars) -> list[tuple[date, float]]:
    return list(zip(bars.dates, bars.closes))


async def records(rows, client) -> dict[str, grading.Record]:
    calls = grading.calls_from_rows(rows)
    symbols = tuple(sorted({c.symbol for c in calls}))
    if not symbols:
        return {}
    bars, _ = await fetch_many(symbols, client, range_="1y")
    bench = await fetch_bars(BENCHMARK, client, range_="1y")
    return grading.grade(calls, {s: _series(b) for s, b in bars.items()},
                         _series(bench), picks_on=picks_by_day())


def _record_text(rec: grading.Record | None) -> str:
    if rec is None or rec.graded == 0:
        pending = f", {rec.pending} pending" if rec else ""
        return f"0 graded{pending} -- unproven"
    verdict = "GRADUATED" if rec.graduated else "unproven"
    dis = (f"; vs momentum {rec.disagreements} calls, {rec.disagreement_mean:+.2f}pp"
           if rec.disagreements else "")
    return (f"{rec.graded} graded ({rec.pending} pending), {rec.mean:+.2f}pp per call, "
            f"t {rec.t:+.2f} -- {verdict}{dis}")


def print_grades(recs: dict[str, grading.Record]) -> None:
    print(f"\n{BOLD}GRADED RECORD{RESET} {DIM}(direction x 21-session excess over the "
          f"S&P; graduates at {op.GRADUATION_CALLS} calls and t >= "
          f"{op.GRADUATION_T}){RESET}")
    for o in op.OPINIONS:
        if o.tier == op.UNPROVEN:
            print(f"  {o.label:<44} {_record_text(recs.get(o.key))}")


def _tag(tier: str) -> str:
    colour = {op.PRIMARY: GREEN, op.UNPROVEN: YELLOW, op.NOISE: DIM}[tier]
    return f"{colour}[{tier}]{RESET}"


async def page(symbol: str, client, rows, recs, picks_day, picks, invested) -> None:
    daily = await fetch_bars(symbol, client, range_="2y")
    closes = daily.closes
    feats = compute(daily)
    print(f"\n{BOLD}{'=' * 74}\n{symbol}{RESET}   last {closes[-1]:,.2f}\n{BOLD}{'=' * 74}{RESET}")

    # ---- primary -------------------------------------------------------
    o = op.BY_KEY["momentum"]
    in_picks = symbol in picks
    call = ("top-10 pick -- the book holds it" if in_picks else "not in the top 10")
    if invested is False:
        call += " (book in CASH: S&P under its 210-day average)"
    mom = f"{feats.momentum_12_1:+.1f}%" if feats.momentum_12_1 is not None else "?"
    print(f"\n  {_tag(o.tier)} {BOLD}{o.label}{RESET}")
    print(f"    {call}; 12-1 momentum {mom}  {DIM}(rebalance {picks_day or 'none yet'}){RESET}")
    print(f"    {DIM}{o.evidence}{RESET}")

    # ---- advisory, unproven ---------------------------------------------
    calls = op.latest_calls(rows, symbol)
    for key in ("tradingagents", "gemma_review"):
        o = op.BY_KEY[key]
        c = calls.get(key)
        print(f"\n  {_tag(o.tier)} {o.label}")
        if c is None:
            print(f"    {DIM}no call on record for {symbol}{RESET}")
        else:
            against = in_picks and c["direction"] == -1
            flag = f"  {RED}DISAGREES with the primary rule{RESET}" if against else ""
            print(f"    {c['call']} on {c['at']}{flag}")
        print(f"    {DIM}record: {_record_text(recs.get(key))}{RESET}")

    # ---- advisory, no edge ------------------------------------------------
    print(f"\n  {_tag(op.NOISE)} {DIM}shown for reference; each tested here with no edge{RESET}")
    value = rsi(closes)
    o = op.BY_KEY["rsi"]
    print(f"    {o.label:<36} {f'{value:.1f}' if value else 'unknown':>10}   {DIM}{o.evidence}{RESET}")
    o = op.BY_KEY["moving_averages"]
    for n in (50, 200):
        if len(closes) >= n:
            gap = (closes[-1] / (sum(closes[-n:]) / n) - 1) * 100
            print(f"    {f'price vs {n}-day average':<36} {gap:>+9.1f}%   {DIM}{o.evidence if n == 50 else ''}{RESET}")
    o = op.BY_KEY["chart_pattern"]
    card = patterns.current(closes, daily.highs, daily.lows, patterns.load_stats())
    print(f"    {o.label:<36} {('present' if card else 'none'):>10}   {DIM}{o.evidence}{RESET}")
    if card:
        print(f"      {DIM}{card.splitlines()[0][:96]}{RESET}")


async def main(symbols: tuple[str, ...], grades_only: bool) -> int:
    rows = read_rows(settings.audit_log_path)
    picks_day, picks, invested = op.momentum_picks(RECORDS)
    async with httpx.AsyncClient(timeout=60) as client:
        recs = await records(rows, client)
        if not grades_only:
            for symbol in symbols:
                try:
                    await page(symbol.upper(), client, rows, recs, picks_day, picks, invested)
                except Exception as exc:                  # noqa: BLE001
                    print(f"\n  {RED}{symbol}: {type(exc).__name__}: {exc}{RESET}")
    print_grades(recs)
    print(f"\n{YELLOW}Advisory tiers never block, size or close a trade.{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--grades", action="store_true", help="only the graded record")
    a = p.parse_args()
    if not a.symbols and not a.grades:
        p.error("give symbols, or --grades")
    raise SystemExit(asyncio.run(main(tuple(a.symbols), a.grades)))
