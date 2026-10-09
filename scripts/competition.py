"""The competition: the operator's picks vs Gemma (rules in scanner/ops/competition.py).

    python scripts/competition.py snapshot    # evening: record books, draw/advance paper books
    python scripts/competition.py standings   # monthly / quarterly / season table

`snapshot` runs in the 13:30 AK outcomes task. Data: data/competition/.
READ-ONLY against the broker; the paper books trade nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import opinions
from scanner.advisor.scan_watch import operator_names
from scanner.config import settings
from scanner.execution import ownership, picks, scorecard
from scanner.execution.alpaca import Alpaca
from scanner.execution.policy import TradingPolicy
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday_context import atr_pct
from scanner.ops import competition as c
from scanner.sources import composite
from scanner.sources.index_history import SP500

DATA = Path("data/competition")
SNAPS, PAPER = DATA / "snapshots.jsonl", DATA / "paper_books.json"
OUT = Path("logs/competition")
POLICY = TradingPolicy()
NAMES = {c.PICKS: "You (picks)", c.GEMMA: "Gemma", c.GHOST_PICKS: "Ghost-you (random 8)",
         c.GHOST_GEMMA: "Ghost-Gemma (random 5)", c.MOMENTUM: "Momentum rule", c.SPX: "S&P 500"}


def load_snaps() -> list[c.Snapshot]:
    if not SNAPS.exists():
        return []
    last: dict[tuple, c.Snapshot] = {}
    for line in SNAPS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            s = c.Snapshot.model_validate_json(line)
            last[(s.day, s.book)] = s            # a re-run on the same day replaces
    return list(last.values())


def load_paper() -> dict[str, c.PaperBook]:
    if not PAPER.exists():
        return {}
    raw = json.loads(PAPER.read_text(encoding="utf-8"))
    return {k: c.PaperBook.model_validate(v) for k, v in raw.items()}


def save_paper(books: dict[str, c.PaperBook]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PAPER.write_text(json.dumps({k: json.loads(v.model_dump_json()) for k, v in books.items()},
                                indent=1), encoding="utf-8")


def ohlc(b):
    return [(d, o, lo, cl) for d, o, lo, cl in zip(b.dates, b.opens, b.lows, b.closes)]


async def pools(client) -> tuple[list[str], list[str]]:
    """(ghost-you pool: the 150 most traded S&P 500 names; ghost-Gemma pool: Gemma's menu)."""
    members = await composite.load_universe(client, (SP500,))
    bars, _ = await fetch_many(tuple(m.symbol for m in members), client, range_="6mo")
    traded = sorted(((sum(cl * v for cl, v in zip(b.closes[-63:], b.volumes[-63:])), s)
                     for s, b in bars.items() if len(b.closes) > 63 and b.closes[-1] >= 5),
                    reverse=True)
    you_pool = [s for _, s in traded[:150] if s not in {"COIN", "MSTR"}]
    menu = (set(operator_names(Path("watchlists/core.txt")))
            | set(operator_names(Path("watchlists/gemma.txt")))
            | set(opinions.momentum_picks()[1])) - set(picks.read_list()[0])
    return you_pool, sorted(menu)


async def open_month(client, month: str, today: date) -> dict[str, c.PaperBook]:
    you_pool, gemma_menu = await pools(client)
    plan = {c.GHOST_PICKS: (c.draw(you_pool, 8, month, c.GHOST_PICKS), True),
            c.GHOST_GEMMA: (c.draw(gemma_menu, POLICY.gemma_max_positions, month, c.GHOST_GEMMA), True),
            c.MOMENTUM: (opinions.momentum_picks()[1], False),
            c.SPX: (("^GSPC",), False)}
    symbols = tuple({s for names, _ in plan.values() for s in names})
    bars, _ = await fetch_many(symbols, client, range_="6mo")
    books = {}
    for who, (names, stopped) in plan.items():
        entries = []
        for s in names:
            b = bars.get(s)
            if b is None or not b.closes:
                continue
            entry = b.closes[-1]
            atr = atr_pct(b) if stopped else None
            stop = entry * (1 - POLICY.picks_stop_atr_multiple * atr / 100) if atr else None
            entries.append(c.PaperName(symbol=s, entry=entry, stop=stop))
        books[f"{month}:{who}"] = c.PaperBook(name=who, month=month, start=today, names=tuple(entries))
    return books


async def snapshot() -> int:
    today = date.today()
    month = c.month_of(today)
    async with httpx.AsyncClient(timeout=120) as client:
        b = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await b._get("/v2/positions")
        owners = ownership.owners(await b._get("/v2/orders", status="all", limit=500, nested="true"))
        realized = scorecard.by_strategy(scorecard.read_rows(settings.audit_log_path))
        DATA.mkdir(parents=True, exist_ok=True)
        with SNAPS.open("a", encoding="utf-8") as f:
            for book in (c.PICKS, c.GEMMA):
                held = [p for p in positions if ownership.owner(p["symbol"], owners) == book]
                done = realized.get(book)
                snap = c.Snapshot(day=today, book=book,
                                  pl_total=(done.total_dollars if done else 0.0)
                                  + sum(float(p["unrealized_pl"]) for p in held),
                                  invested=sum(float(p["cost_basis"]) for p in held))
                f.write(snap.model_dump_json() + "\n")
                print(f"snapshot {book}: P/L {snap.pl_total:+,.2f} on {snap.invested:,.0f} invested")
        books = load_paper()
        if not any(k.startswith(month) for k in books):
            books.update(await open_month(client, month, today))
            print(f"opened {month} paper books: " + "; ".join(
                f"{v.name} {[n.symbol for n in v.names]}" for k, v in books.items() if k.startswith(month)))
        current = {k: v for k, v in books.items() if k.startswith(month)}
        symbols = tuple({n.symbol for v in current.values() for n in v.names})
        bars, _ = await fetch_many(symbols, client, range_="3mo", ttl_seconds=FRESH_S)
        for k, v in current.items():
            books[k] = c.advance(v, {s: ohlc(bars[s]) for s in bars})
        save_paper(books)
    return 0


#: Prices for the paper books (ghosts, momentum, S&P) may be at most this old.
#: Run at 13:30 AK, after the close, so a morning cache is refetched with the
#: day's close -- otherwise they lag the live books by a day (2026-10-09).
FRESH_S = 3600


def with_start(snaps: list[c.Snapshot]) -> list[c.Snapshot]:
    """Both books started from zero on the season's first day (2026-10-07,
    bought at that open), so their first evening's P/L already counts: anchor
    each book at P/L 0 the day before its first snapshot."""
    out = list(snaps)
    for book in (c.PICKS, c.GEMMA):
        mine = sorted((s for s in snaps if s.book == book), key=lambda s: s.day)
        if mine:
            out.append(c.Snapshot(day=date.fromordinal(mine[0].day.toordinal() - 1), book=book,
                                  pl_total=0.0, invested=mine[0].invested))
    return out


def standings_text(snaps, books, last_close) -> str:
    snaps = with_start(snaps)
    months = sorted({c.month_of(s.day) for s in snaps} | {v.month for v in books.values()})
    results = []
    for m in months:
        days = sorted({s.day for s in snaps if c.month_of(s.day) == m})
        before = [s.day for s in snaps if c.month_of(s.day) < m]
        scores = {}
        if days:
            first = max(before) if before else days[0]
            for book in (c.PICKS, c.GEMMA):
                r = c.book_return(snaps, book, first, days[-1])
                if r is not None:
                    scores[book] = r
        for k, v in books.items():
            if v.month == m:
                scores[v.name] = c.paper_return(v, last_close.get(m, {}))
        results.append(c.MonthResult(month=m, scores=scores, winner=c.month_winner(scores)))
    lines = ["# Competition standings", "",
             "You vs Gemma, month by month. Ghosts are random picks under the same rules: beating "
             "your ghost means the picking, not the list, did the work.", ""]
    order = [c.PICKS, c.GEMMA, c.GHOST_PICKS, c.GHOST_GEMMA, c.MOMENTUM, c.SPX]
    for r in results:
        lines += [f"## {r.month}" + (" (in progress)" if r is results[-1] else ""), "",
                  "| Competitor | Return |", "|---|---|"]
        for k in order:
            if k in r.scores:
                lines.append(f"| {NAMES[k]} | {r.scores[k]:+.2f}% |")
        win = NAMES[r.winner] if r.winner else "tie / not enough data"
        ghost = []
        for real, g in ((c.PICKS, c.GHOST_PICKS), (c.GEMMA, c.GHOST_GEMMA)):
            if real in r.scores and g in r.scores:
                ghost.append(f"{NAMES[real]} {'beat' if r.scores[real] > r.scores[g] else 'did not beat'} its ghost")
        lines += ["", f"**Head to head: {win}.**" + (" " + "; ".join(ghost) + "." if ghost else ""), ""]
    by_q = defaultdict(list)
    for r in results:
        by_q[c.quarter_of(r.month)].append(r)
    lines += ["## Quarters", ""]
    for q, ms in by_q.items():
        w = c.quarter_winner(ms)
        tally = {k: sum(1 for m in ms if m.winner == k) for k in (c.PICKS, c.GEMMA)}
        lines.append(f"- {q}: months won -- you {tally[c.PICKS]}, Gemma {tally[c.GEMMA]}; "
                     f"leader {NAMES[w] if w else 'tied'}")
    season = {k: sum(r.scores.get(k, 0.0) for r in results) for k in (c.PICKS, c.GEMMA)}
    p = c.prob_better(c.daily_returns(snaps, c.GEMMA), c.daily_returns(snaps, c.PICKS))
    how_sure = (f"Probability Gemma is really the better trader so far: {p:.0%} "
                f"(you: {1 - p:.0%}), from the daily returns resampled."
                if p is not None else
                f"How sure is the lead? Needs {c.MIN_DAYS_FOR_PROBABILITY} trading days of "
                "snapshots; until then the lead is mostly noise.")
    lines += ["", f"## Season {results[0].month[:4] if results else ''}", "",
              f"You {season[c.PICKS]:+.2f}%, Gemma {season[c.GEMMA]:+.2f}% (sum of monthly returns). "
              f"Both books run 50/50 now; from {c.STAKES_START:%b %d %Y}, at each quarter's end the "
              f"loser gives {c.QUARTER_TRANSFER_PCT:.0f}% of the capital to the winner (never past "
              f"75/25). Q4 2026 is the trial quarter.",
              f"Season ends Dec 31; the winner gets +{c.REWARD_POINTS:.0f} points of the account "
              "for next season, taken from the other book.", "", how_sure, ""]
    return "\n".join(lines)


async def standings() -> int:
    snaps, books = load_snaps(), load_paper()
    async with httpx.AsyncClient(timeout=120) as client:
        symbols = tuple({n.symbol for v in books.values() for n in v.names})
        bars, _ = (await fetch_many(symbols, client, range_="6mo", ttl_seconds=FRESH_S)
                   if symbols else ({}, ()))
    last_close: dict[str, dict[str, float]] = defaultdict(dict)
    for v in books.values():
        for n in v.names:
            b = bars.get(n.symbol)
            if b is not None:
                inside = [cl for d, cl in zip(b.dates, b.closes) if c.month_of(d) <= v.month]
                if inside:
                    last_close[v.month][n.symbol] = inside[-1]
    text = standings_text(snaps, books, last_close)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "standings.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["snapshot", "standings"])
    cmd = p.parse_args().command
    raise SystemExit(asyncio.run(snapshot() if cmd == "snapshot" else standings()))
