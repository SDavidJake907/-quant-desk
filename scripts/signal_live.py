"""Run every Pine signal live on the day's fast movers, in VIRTUAL paper books.

    python scripts/signal_live.py            # one pass (the 5-minute task runs this)
    python scripts/signal_live.py --report   # rewrite today's leaderboard only

Operator 2026-10-21: "start on this, with the fast moving stocks.. for the
buy/sell signal. find what works best and test it and run it live/papermode".
Rules: scanner/signals/rules.py; books: scanner/signals/paper.py; the history
test is TRIALS #80 (research_gpu/signal_lab.py).

VIRTUAL MONEY: $1,000 a trade, never an order -- the Alpaca account, the picks
and Gemma's book are untouched, and Gemma does not see this yet.

Each pass, in regular hours (9:30-16:00 ET):
  1  universe: the 30 fastest liquid S&P 500 + MidCap names by 20-day ATR %,
     fixed once a day (data/signals/universe/DATE.json)
  2  live 5m / 15m / 60m candles from Yahoo (consolidated), the forming candle
     dropped; the live price is the newest 5-minute close
  3  every version (11 signals x plain/+ADX x 3 timeframes) steps its book
Files: data/signals/paper_state.json, paper_trades.jsonl; logs/signals/DATE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, time
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import morning_ideas  # scripts/ is on the path when this script runs

from scanner.features.bars import fetch_many
from scanner.features.intraday import EXCHANGE_TZ, drop_incomplete, fetch_intraday
from scanner.ops import ideas
from scanner.signals import indicators as ind
from scanner.signals import paper, rules
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500

DATA = Path("data/signals")
STATE, TRADES, UNIVERSE = DATA / "paper_state.json", DATA / "paper_trades.jsonl", DATA / "universe"
OUT = Path("logs/signals")
UNIVERSE_N = 30
TIMEFRAMES = {5: "5d", 15: "1mo", 60: "3mo"}
OPEN, CLOSE, FRIDAY_FLAT = time(9, 30), time(16, 0), time(15, 45)
CONCURRENCY = 8


def label(minutes: int) -> str:
    return "1h" if minutes == 60 else f"{minutes}m"


async def universe(client, today) -> list[str]:
    f = UNIVERSE / f"{today.isoformat()}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    members = await composite.load_universe(client, (SP500, SP400))
    bars, _ = await fetch_many(tuple(sorted({m.symbol for m in members})), client, range_="1y")
    stats = {s: st for s, b in bars.items()
             if (st := morning_ideas.fast_stats(morning_ideas.completed(b, today))) is not None}
    _, fastest = ideas.fast_movers(stats)
    names = [p.symbol for p in fastest[:UNIVERSE_N]]
    UNIVERSE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(names), encoding="utf-8")
    return names


def load_state() -> dict:
    try:
        raw = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"positions": {}, "seen": {}}
    raw["positions"] = {v: {s: paper.Position.model_validate(p) for s, p in book.items()}
                        for v, book in raw.get("positions", {}).items()}
    return raw


def save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    out = {"positions": {v: {s: json.loads(p.model_dump_json()) for s, p in book.items()}
                         for v, book in state["positions"].items() if book},
           "seen": state["seen"]}
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(STATE)


async def candles(client, symbol: str, sem) -> dict[int, object]:
    async with sem:
        out = {}
        for minutes, rng in TIMEFRAMES.items():
            try:
                out[minutes] = await fetch_intraday(symbol, client, minutes=minutes, range_=rng)
            except Exception as exc:                          # noqa: BLE001 -- one name, not the run
                print(f"  {symbol} {minutes}m: {exc}")
        return out


def step_symbol(symbol: str, bars: dict, state: dict, now: datetime, friday: bool) -> list[paper.Closed]:
    five = bars.get(5)
    if five is None or len(five) < 2:
        return []
    price = five.closes[-1]
    closed = []
    for minutes, raw in bars.items():
        b = drop_incomplete(raw, minutes=minutes, now=now)
        if len(b) < 150:
            continue
        o, h, lo, c, v = (list(x) for x in (b.opens, b.highs, b.lows, b.closes, b.volumes))
        a = ind.atr(h, lo, c, 22)[-1]
        adx = ind.adx(h, lo, c)[-1]
        for name, (_, rule) in rules.RULES.items():
            st = rule(o, h, lo, c, v)
            for suffix, gate in (("", True), ("+adx", adx is not None and adx > rules.ADX_MIN)):
                version = f"{label(minutes)} {name}{suffix}"
                book = state["positions"].setdefault(version, {})
                key = f"{version}|{symbol}"
                seen = state["seen"].get(key)
                pos, done, newest = paper.step(
                    version, symbol, times=list(b.times), lows=lo, state=st, gate_ok=gate, atr_now=a,
                    price=price, now=now, position=book.get(symbol),
                    last_seen=datetime.fromisoformat(seen) if seen else None, friday_flat=friday)
                if pos is None:
                    book.pop(symbol, None)
                else:
                    book[symbol] = pos
                if newest is not None:
                    state["seen"][key] = newest.isoformat()
                if done is not None:
                    closed.append(done)
    return closed


def read_trades() -> list[paper.Closed]:
    if not TRADES.exists():
        return []
    out = []
    for line in TRADES.read_text(encoding="utf-8").splitlines():
        try:
            out.append(paper.Closed.model_validate_json(line))
        except ValueError:
            continue
    return out


def report(today, names: list[str], state: dict) -> str:
    trades = read_trades()
    lab = {}
    try:
        lab = json.loads((DATA / "lab_verdict.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    lines = [f"# Signal paper books {today}", "",
             "VIRTUAL money, $1,000 a trade -- no orders, Gemma does not see this. "
             "Rules: scanner/signals/rules.py; history: TRIALS #80.", "",
             f"Fast movers today ({len(names)}): {', '.join(names)}", ""]
    for title, rows in (("Today", [t for t in trades if t.exit_at.date() == today]), ("Since the start", trades)):
        board = paper.leaderboard(rows)
        lines += [f"## {title}", ""]
        if not board:
            lines += ["No closed trades yet.", ""]
            continue
        lines += ["| # | Version | Trades | Win % | Sum of trades | $ | History (#80) |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {i} | {s.version} | {s.trades} | {s.wins / s.trades * 100:.0f}% | {s.net_pct:+.2f}% | "
                  f"{s.dollars:+,.2f} | {lab.get(s.version, '')} |" for i, s in enumerate(board, 1)]
        lines.append("")
    held = sum(len(b) for b in state["positions"].values())
    lines += [f"Open paper positions: {held}", ""]
    return "\n".join(lines) + "\n"


async def main(report_only: bool) -> int:
    now = datetime.now(EXCHANGE_TZ)
    today = now.date()
    state = load_state()
    async with httpx.AsyncClient(timeout=60) as client:
        names = await universe(client, today)
        in_hours = now.weekday() < 5 and OPEN <= now.time() < CLOSE
        if in_hours and not report_only:
            sem = asyncio.Semaphore(CONCURRENCY)
            got = await asyncio.gather(*(candles(client, s, sem) for s in names))
            friday = now.weekday() == 4 and now.time() >= FRIDAY_FLAT
            # Names that left today's universe still close their open trades.
            held = {s for b in state["positions"].values() for s in b} - set(names)
            if held:
                got += await asyncio.gather(*(candles(client, s, sem) for s in sorted(held)))
            closed = []
            for symbol, bars in zip([*names, *sorted(held)], got):
                closed += step_symbol(symbol, bars, state, now, friday)
            if closed:
                DATA.mkdir(parents=True, exist_ok=True)
                with TRADES.open("a", encoding="utf-8") as f:
                    for t in closed:
                        f.write(t.model_dump_json() + "\n")
            save_state(state)
            opened = sum(len(b) for b in state["positions"].values())
            print(f"{now:%H:%M} ET: {len(names)} names, {len(closed)} closed, {opened} open")
        elif not report_only:
            print(f"{now:%H:%M} ET: outside regular hours -- report only")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today.isoformat()}.md").write_text(report(today, names, state), encoding="utf-8")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="store_true", help="rewrite today's leaderboard only")
    raise SystemExit(asyncio.run(main(p.parse_args().report)))
