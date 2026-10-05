"""Scan any list of stocks with the live screen -- view only, never trades.

    python scripts/watch_list.py                         # watchlists/kraken.txt
    python scripts/watch_list.py --file watchlists/x.txt
    python scripts/watch_list.py NVDA WDC STX            # names on the command line

READ-ONLY. No broker call, no order code, no audit row. The live system
trades the S&P 500 only, and widening that ends the evaluation window
(FREEZE.md); this shows what the same screen sees on other names meanwhile.

Per name, from the live code (same fetch, same 30m bars with the forming bar
dropped, same scan(), freshness, liveness and session checks as trade.py):
    price, day %, $ volume per bar (live floor $20M), price floor $5
    SETUP NOW    a fresh, live setup the live scan would pass to its gates
    last setup   the newest setup in the bars and how many bars ago
    trend        12-1 month return vs the S&P 500, and price vs its 200-day
                 average -- the momentum engine's view, for context only
Stocks only: the list file is plain tickers, one per line, # for comments.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session, is_stale_session
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ, drop_incomplete, fetch_intraday, max_range
from scanner.screens.smc import is_live_setup, scan

sys.path.insert(0, "scripts")
from relative_strength_test import momentum_12_1  # noqa: E402

V = TradingPolicy()
DEFAULT_FILE = Path("watchlists/kraken.txt")


def load_symbols(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip().upper()
        if name and name not in names:
            names.append(name)
    return names


def dollar_volume(bars, window: int = 20) -> float:
    pairs = list(zip(bars.closes[-window:], bars.volumes[-window:]))
    return statistics.fmean(c * v for c, v in pairs) if pairs else 0.0


def setups_for(bars, today) -> tuple[object | None, object | None]:
    """(fresh live setup or None, newest setup of any age or None)."""
    found = scan(bars, k=V.swing_k, min_reward_risk=V.min_reward_risk,
                 min_risk_pct=V.min_risk_pct, require_value_sweep=V.require_value_sweep)
    newest = max(found, key=lambda s: s.trigger_index, default=None)
    for setup in sorted(found, key=lambda s: -s.trigger_index):
        age = len(bars) - 1 - setup.trigger_index
        if age >= V.fresh_bars or not is_live_setup(bars, setup):
            continue
        if V.block_overnight_entry and (
                crosses_session(bars, setup.trigger_index)
                or is_stale_session(bars, setup.trigger_index, session=today)):
            continue
        return setup, newest
    return None, newest


def trend(daily, bench) -> str:
    if daily is None or len(daily.closes) < 210:
        return "trend n/a"
    closes = list(daily.closes)
    sma200 = statistics.fmean(closes[-200:])
    stock, index = momentum_12_1(closes), momentum_12_1(list(bench.closes))
    rs = f"{stock - index:+6.1f}pp vs S&P" if stock is not None and index is not None else "   RS n/a      "
    side = "above" if closes[-1] > sma200 else "BELOW"
    return f"12-1 {rs}, {side} 200d"


async def main(symbols: list[str]) -> int:
    print(f"watch list: {len(symbols)} names, 30m bars, policy {V.fingerprint} -- VIEW ONLY\n")
    today = datetime.now(tz=EXCHANGE_TZ).date()
    async with httpx.AsyncClient(timeout=60) as client:
        loaded = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=V.interval_minutes, range_=max_range(V.interval_minutes))
              for s in symbols), return_exceptions=True)
        bench = await fetch_bars("^GSPC", client, range_="2y")
        daily, _ = await fetch_many(tuple(symbols), client, range_="2y")
    live = []
    for symbol, bars in zip(symbols, loaded):
        if isinstance(bars, Exception) or not bars.closes:
            print(f"  {symbol:<6} no bars ({type(bars).__name__ if isinstance(bars, Exception) else 'empty'})")
            continue
        bars = drop_incomplete(bars, minutes=V.interval_minutes)
        price = bars.closes[-1]
        prior = [c for t, c in zip(bars.times, bars.closes) if t.date() < bars.times[-1].date()]
        day = f"{(price / prior[-1] - 1) * 100:+6.2f}%" if prior else "   n/a"
        dvol = dollar_volume(bars)
        tradeable = price >= V.min_price and dvol >= V.min_dollar_volume
        fresh, newest = setups_for(bars, today)
        if fresh is not None:
            risk = fresh.entry_price - fresh.stop_price
            note = (f"SETUP NOW  entry {fresh.entry_price:.2f} stop {fresh.stop_price:.2f} "
                    f"target {fresh.target_price:.2f}  R:R {(fresh.target_price - fresh.entry_price) / risk:.1f}")
            live.append(symbol)
        elif newest is not None:
            note = f"last setup {len(bars) - 1 - newest.trigger_index} bars ago"
        else:
            note = "no setup in history"
        flag = "" if tradeable else "  (below live floors)"
        print(f"  {symbol:<6}{price:>10.2f} {day}  ${dvol / 1e6:>7.1f}M/bar  "
              f"{trend(daily.get(symbol), bench):<34} {note}{flag}")
    print(f"\n{len(live)} fresh setup(s): {', '.join(live) or 'none'}. "
          f"View only -- nothing was sent anywhere.")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--file", type=Path, default=DEFAULT_FILE)
    a = p.parse_args()
    names = [s.upper() for s in a.symbols] or load_symbols(a.file)
    raise SystemExit(asyncio.run(main(names)))
