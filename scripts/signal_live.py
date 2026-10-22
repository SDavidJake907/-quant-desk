"""Run every Pine signal live on the day's fast movers, in VIRTUAL paper books.

    python scripts/signal_live.py            # one pass (the 5-minute task runs this)
    python scripts/signal_live.py --report   # rewrite today's leaderboard only

Operator 2026-10-21: "start on this, with the fast moving stocks.. for the
buy/sell signal. find what works best and test it and run it live/papermode".
Rules: scanner/signals/rules.py; books: scanner/signals/paper.py; the history
test is TRIALS #80 (research_gpu/signal_lab.py).

VIRTUAL MONEY: $1,000 a trade, never an order -- the Alpaca account, the picks
and Gemma's book are untouched, and Gemma does not see this yet.

Each pass (the task runs every 5 minutes, around the clock):
  1  universe: the 30 fastest liquid S&P 500 + MidCap names by 20-day ATR %,
     fixed once a day (data/signals/universe/DATE.json)
  2  REGULAR books, 9:30-16:00 ET: live 5m / 15m / 60m regular-session candles
     from Yahoo, the forming candle dropped; 66 versions; flat Friday 15:45
  3  24h BOOKS, every session Sunday 20:00 -> Friday 20:00 ET (operator:
     "remember 24/5"): one 5-minute stream from Yahoo pre/regular/after-hours
     plus Alpaca BOATS overnight (signals/candles24.py), and its 15m grouping;
     44 versions named "24h ..."; flat Friday 19:45. Overnight candles are thin,
     so these books are scored apart and never mixed with the regular ones.
  4  SWEEP books, regular hours, 5m and 15m (operator 2026-10-22, from the ITI
     liquidity-sweep article): signals/sweep.py -- a prior-day or 20-candle
     low swept, closed back inside on 2x volume; half at VWAP, rest at the range
     high, stop under the wick, out by 15:55. "+scalp" takes all at VWAP.
     Universe: the fast movers plus the 20 largest companies.
  5  OVERSOLD books, daily and weekly (TRIALS #94 -> forward test #95, operator
     2026-10-22): signals/slow.py -- Connors RSI and MFI, plain and +ADX, on the
     S&P 500; daily books once a day at the first regular-hours pass, weekly books
     at the first pass of a new week; hold through weekends.
Files: data/signals/paper_state.json, paper_trades.jsonl; logs/signals/DATE.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import morning_ideas  # scripts/ is on the path when this script runs

from scanner.features.bars import fetch_many
from scanner.features.intraday import EXCHANGE_TZ, drop_incomplete, fetch_intraday
from scanner.ops import ideas
from scanner.signals import candles24 as c24
from scanner.signals import indicators as ind
from scanner.signals import paper, rules, slow, sweep
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500

DATA = Path("data/signals")
STATE, TRADES, UNIVERSE = DATA / "paper_state.json", DATA / "paper_trades.jsonl", DATA / "universe"
OUT = Path("logs/signals")
UNIVERSE_N = 30
TIMEFRAMES = {5: "5d", 15: "1mo", 60: "3mo"}
OPEN, CLOSE, FRIDAY_FLAT = time(9, 30), time(16, 0), time(15, 45)
EXT_START, EXT_END, FRIDAY_FLAT_24H = time(4, 0), time(20, 0), time(19, 45)
FRAMES_24H = (5, 15)
#: Minutes past a candle's close it may still trigger an entry (feed delay).
STALE_GRACE = 20
#: The daily/weekly books act at the first regular pass from here (after the open settles).
SLOW_FROM = time(9, 35)
SWEEP_FRAMES = ("5m", "15m")
BIGCAP = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "JPM", "LLY",
          "BRK.B", "V", "WMT", "XOM", "UNH", "MA", "COST", "ORCL", "JNJ", "HD")


def session(now: datetime) -> str:
    """regular / pre / after / overnight / closed, from the Eastern clock (24/5:
    Sunday 20:00 to Friday 20:00). Holidays read as open and simply see no new candles."""
    wd, t = now.weekday(), now.time()
    if wd == 5 or (wd == 4 and t >= EXT_END) or (wd == 6 and t < EXT_END):
        return "closed"
    if OPEN <= t < CLOSE and wd < 5:
        return "regular"
    if EXT_START <= t < OPEN:
        return "pre"
    if CLOSE <= t < EXT_END:
        return "after"
    return "overnight"
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
    raw["sweep"] = {v: {s: sweep.Open.model_validate(p) for s, p in book.items()}
                    for v, book in raw.get("sweep", {}).items()}
    return raw


def save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    out = {"positions": {v: {s: json.loads(p.model_dump_json()) for s, p in book.items()}
                         for v, book in state["positions"].items() if book},
           "sweep": {v: {s: json.loads(p.model_dump_json()) for s, p in book.items()}
                     for v, book in state.get("sweep", {}).items() if book},
           "seen": state["seen"],
           # 2026-10-22: these two were not saved, so the oversold books re-ran every pass.
           "slow_daily_done": state.get("slow_daily_done"),
           "slow_weekly_done": state.get("slow_weekly_done")}
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


def regular_frames(bars: dict, now: datetime) -> tuple[dict, float | None]:
    """label -> (times, o, h, l, c, v) of completed regular-session candles; live price."""
    five = bars.get(5)
    if five is None or len(five) < 2:
        return {}, None
    frames = {}
    for minutes, raw in bars.items():
        b = drop_incomplete(raw, minutes=minutes, now=now)
        frames[label(minutes)] = (list(b.times), *(list(x) for x in (b.opens, b.highs, b.lows, b.closes, b.volumes)))
    return frames, five.closes[-1]


def frames_24h(five: list, now: datetime) -> tuple[dict, float | None]:
    if len(five) < 2:
        return {}, None
    frames = {}
    for minutes in FRAMES_24H:
        rows = c24.completed(five if minutes == 5 else c24.group(five, minutes), minutes, now)
        frames[f"24h {label(minutes)}"] = ([r[0] for r in rows], *([r[k] for r in rows] for k in range(1, 6)))
    return frames, five[-1][4]


def step_symbol(symbol: str, frames: dict, price: float | None, state: dict, now: datetime,
                friday: bool) -> list[paper.Closed]:
    if price is None:
        return []
    closed = []
    for tf, (times, o, h, lo, c, v) in frames.items():
        if len(c) < 150:
            continue
        a = ind.atr(h, lo, c, 22)[-1]
        adx = ind.adx(h, lo, c)[-1]
        # FRESH OR NOTHING (2026-10-22, first overnight pass): a thin name can go an
        # hour without a trade, and BOATS candles arrive ~15-20 min late on this
        # plan. An entry needs a signal candle that closed within STALE_GRACE.
        minutes = int(tf.split()[-1].rstrip("mh")) * (60 if tf.endswith("h") else 1)
        fresh = (now - times[-1].astimezone(EXCHANGE_TZ)).total_seconds() / 60 <= minutes + STALE_GRACE
        for name, (_, rule) in rules.RULES.items():
            st = rule(o, h, lo, c, v)
            for suffix, gate in (("", fresh), ("+adx", fresh and adx is not None and adx > rules.ADX_MIN)):
                version = f"{tf} {name}{suffix}"
                book = state["positions"].setdefault(version, {})
                key = f"{version}|{symbol}"
                seen = state["seen"].get(key)
                pos, done, newest = paper.step(
                    version, symbol, times=times, lows=lo, state=st, gate_ok=gate, atr_now=a,
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


def step_sweep(symbol: str, frames: dict, price: float | None, state: dict, now: datetime) -> list[paper.Closed]:
    """The liquidity-sweep books for one symbol, regular hours only."""
    if price is None:
        return []
    closed: list[paper.Closed] = []
    books = state.setdefault("sweep", {})
    for tf in SWEEP_FRAMES:
        if tf not in frames:
            continue
        times, o, h, lo, c, v = frames[tf]
        if len(c) < 60:
            continue
        minutes = int(tf.rstrip("m"))
        fresh = (now - times[-1].astimezone(EXCHANGE_TZ)).total_seconds() / 60 <= minutes + STALE_GRACE
        key = f"{tf} sweep|{symbol}"
        new_bar = state["seen"].get(key) != times[-1].isoformat()
        setup = sweep.detect(times, o, h, lo, c, v, price=price) if (fresh and new_bar) else None
        state["seen"][key] = times[-1].isoformat()
        for version, scalp in ((f"{tf} sweep", False), (f"{tf} sweep+scalp", True)):
            book = books.setdefault(version, {})
            pos = book.get(symbol)
            if pos is not None:
                pos, done = sweep.manage(version, pos, times=times, highs=h, lows=lo, price=price,
                                         now=now, scalp=scalp)
                closed += done
            elif setup is not None:
                pos = sweep.Open(symbol=symbol, entry=price, entry_at=now, stop=setup.stop,
                                 target1=setup.target1, target2=setup.target2, signal_bar=setup.signal_bar)
            if pos is None:
                book.pop(symbol, None)
            else:
                book[symbol] = pos
    return closed


async def step_slow(client, state: dict, now: datetime) -> list[paper.Closed]:
    """Daily and weekly oversold books: once a day / once a week, on completed candles."""
    today = now.date()
    week = list(today.isocalendar()[:2])
    do_daily = state.get("slow_daily_done") != today.isoformat()
    do_weekly = state.get("slow_weekly_done") != week
    if not (do_daily or do_weekly):
        return []
    members = await composite.load_universe(client, (SP500,))
    bars, _ = await fetch_many(tuple(sorted({m.symbol for m in members})), client, range_="2y", ttl_seconds=600)
    closed: list[paper.Closed] = []
    for s, b in bars.items():
        rows = list(zip(b.dates, b.opens, b.highs, b.lows, b.closes, b.volumes))
        if not rows or rows[-1][0] != today:
            continue                      # no live candle for today yet: no price to trade at
        price = rows[-1][4]
        for tf, done, cands in (("daily", do_daily, slow.completed_daily(rows, today)),
                                ("weekly", do_weekly, slow.completed_weekly(rows, today))):
            if not done or len(cands) < 60:
                continue
            times = [datetime(d.year, d.month, d.day, 16, tzinfo=EXCHANGE_TZ) for d, *_ in cands]
            o, h, lo, c, v = ([x[k] for x in cands] for k in range(1, 6))
            a = ind.atr(h, lo, c, 22)[-1]
            adx = ind.adx(h, lo, c)[-1]
            for name in slow.SLOW_RULES:
                st = rules.RULES[name][1](o, h, lo, c, v)
                for suffix, gate in (("", True), ("+adx", adx is not None and adx > rules.ADX_MIN)):
                    version = f"{tf} {name}{suffix}"
                    book = state["positions"].setdefault(version, {})
                    key = f"{version}|{s}"
                    seen = state["seen"].get(key)
                    pos, done_trade, newest = paper.step(
                        version, s, times=times, lows=lo, state=st, gate_ok=gate, atr_now=a, price=price,
                        now=now, position=book.get(s),
                        last_seen=datetime.fromisoformat(seen) if seen else None, friday_flat=False)
                    if pos is None:
                        book.pop(s, None)
                    else:
                        book[s] = pos
                    if newest is not None:
                        state["seen"][key] = newest.isoformat()
                    if done_trade is not None:
                        closed.append(done_trade)
    if do_daily:
        state["slow_daily_done"] = today.isoformat()
    if do_weekly:
        state["slow_weekly_done"] = week
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
    slow_ = [t for t in trades if t.version.startswith(("daily ", "weekly "))]
    regular = [t for t in trades if not t.version.startswith(("24h", "daily ", "weekly "))]
    ext = [t for t in trades if t.version.startswith("24h")]
    for title, rows in (("Regular hours -- today", [t for t in regular if t.exit_at.date() == today]),
                        ("Regular hours -- since the start", regular),
                        ("24h books (pre-market, after-hours, overnight) -- today",
                         [t for t in ext if t.exit_at.date() == today]),
                        ("24h books -- since the start", ext),
                        ("Oversold books, daily and weekly (TRIALS #95 forward test) -- since the start", slow_)):
        board = paper.leaderboard(rows)
        lines += [f"## {title}", ""]
        if not board:
            lines += ["No closed trades yet.", ""]
            continue
        lines += ["| # | Version | Trades | Win % | Sum of trades | $ | History (#80) |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {i} | {s.version} | {s.trades} | {s.wins / s.trades * 100:.0f}% | {s.net_pct:+.2f}% | "
                  f"{s.dollars:+,.2f} | {lab.get(s.version, '')} |" for i, s in enumerate(board, 1)]
        lines.append("")
    held = sum(len(b) for b in state["positions"].values()) + sum(len(b) for b in state.get("sweep", {}).values())
    lines += [f"Open paper positions: {held}", ""]
    return "\n".join(lines) + "\n"


async def main(report_only: bool) -> int:
    now = datetime.now(EXCHANGE_TZ)
    today = now.date()
    state = load_state()
    where = session(now)
    async with httpx.AsyncClient(timeout=60) as client:
        names = await universe(client, today)
        if where != "closed" and not report_only:
            held = sorted(({s for b in state["positions"].values() for s in b}
                           | {s for b in state.get("sweep", {}).values() for s in b}) - set(names))
            symbols = [*names, *held]          # names that left the universe still close their trades
            extra = [s for s in BIGCAP if s not in symbols]   # sweep books only
            sem = asyncio.Semaphore(CONCURRENCY)
            closed: list[paper.Closed] = []
            if where == "regular":
                got = await asyncio.gather(*(candles(client, s, sem) for s in [*symbols, *extra]))
                friday = now.weekday() == 4 and now.time() >= FRIDAY_FLAT
                for symbol, bars in zip([*symbols, *extra], got):
                    frames, price = regular_frames(bars, now)
                    if symbol in symbols:
                        closed += step_symbol(symbol, frames, price, state, now, friday)
                    closed += step_sweep(symbol, frames, price, state, now)
                if now.time() >= SLOW_FROM:
                    try:
                        closed += await step_slow(client, state, now)
                    except Exception as exc:                  # noqa: BLE001 -- never sink the run
                        print(f"  oversold books skipped this pass: {exc}")

            async def ext(s):
                async with sem:
                    try:
                        return await c24.yahoo_5m(s, client)
                    except Exception as exc:                  # noqa: BLE001 -- one name, not the run
                        print(f"  {s} 24h: {exc}")
                        return []

            # The 24h books run in every session.
            yahoo = await asyncio.gather(*(ext(s) for s in symbols))
            try:
                boats = await c24.boats_5m(symbols, client, now - timedelta(days=6))
            except Exception as exc:                          # noqa: BLE001
                print(f"  BOATS overnight candles unavailable: {exc}")
                boats = {}
            friday24 = now.weekday() == 4 and now.time() >= FRIDAY_FLAT_24H
            for symbol, y in zip(symbols, yahoo):
                frames, price = frames_24h(c24.merge(y, boats.get(symbol, [])), now)
                closed += step_symbol(symbol, frames, price, state, now, friday24)
            if closed:
                DATA.mkdir(parents=True, exist_ok=True)
                with TRADES.open("a", encoding="utf-8") as f:
                    for tr in closed:
                        f.write(tr.model_dump_json() + "\n")
            save_state(state)
            opened = sum(len(b) for b in state["positions"].values()) + sum(len(b) for b in state.get("sweep", {}).values())
            print(f"{now:%H:%M} ET [{where}]: {len(names)} names, {len(closed)} closed, {opened} open")
        elif not report_only:
            print(f"{now:%H:%M} ET: market closed (weekend) -- report only")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today.isoformat()}.md").write_text(report(today, names, state), encoding="utf-8")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="store_true", help="rewrite today's leaderboard only")
    raise SystemExit(asyncio.run(main(p.parse_args().report)))
