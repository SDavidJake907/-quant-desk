"""The paused sweep scanner as a ghost book (rules in scanner/ops/ghost_scanner.py).

    python scripts/ghost_scanner.py

Fetches the S&P 500's 30-minute bars, runs the same smc scan the live trader
ran, and replays what it would have bought since the last run (first run:
from 2026-10-07). Open ghost trades are settled on the new bars. Runs in the
13:30 AK outcomes task before the standings; writes
data/competition/ghost_scanner.json. PAPER ONLY: no broker, no orders.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import drop_incomplete, fetch_intraday, max_range
from scanner.ops import ghost_scanner as gs
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import SP500

OUT = Path("data/competition/ghost_scanner.json")
POLICY = TradingPolicy()
WINDOW = 20


def liquid(bars) -> bool:
    """trade.py's tradeability floor, evaluated as the bars stood at the trigger."""
    closes, volumes = bars.closes[-WINDOW:], bars.volumes[-WINDOW:]
    if not closes or closes[-1] < POLICY.min_price:
        return False
    spans = [(h - l) / c * 100 for h, l, c in
             zip(bars.highs[-WINDOW:], bars.lows[-WINDOW:], closes) if c]
    if (sum(spans) / len(spans) if spans else 0.0) < POLICY.min_range_pct:
        return False
    return sum(v * c for v, c in zip(volumes, closes)) / len(closes) >= POLICY.min_dollar_volume


def setups_for(bars) -> tuple:
    found = scan(bars, k=POLICY.swing_k, min_reward_risk=POLICY.min_reward_risk,
                 min_risk_pct=POLICY.min_risk_pct,
                 require_value_sweep=POLICY.require_value_sweep)
    return tuple(s for s in found if liquid(bars.upto(s.trigger_index)))


async def main() -> int:
    existing, through = gs.load(OUT.read_text(encoding="utf-8")) if OUT.exists() else ((), None)
    since = through or gs.START
    minutes = POLICY.interval_minutes
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, (SP500,))
        symbols = sorted({m.symbol for m in universe} | {t.symbol for t in existing})
        sectors = {m.symbol: m.sector for m in universe} | {t.symbol: t.sector for t in existing}
        loaded = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols), return_exceptions=True)
    bars, failed = {}, []
    for s, b in zip(symbols, loaded):
        if isinstance(b, Exception) or not len(b):
            failed.append(s)
            continue
        bars[s] = drop_incomplete(b, minutes=minutes)
    if not bars:
        print("no bars fetched -- ghost unchanged")
        return 1
    setups = {s: setups_for(b) for s, b in bars.items() if len(b)}
    trades = gs.replay(bars, setups, sectors, POLICY, since=since, existing=existing)
    newest = max(b.times[-1] for b in bars.values() if len(b))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(gs.dump(trades, through=max(newest, since)), encoding="utf-8")
    tmp.replace(OUT)

    last = {s: b.closes[-1] for s, b in bars.items() if len(b)}
    added = len(trades) - len(existing)
    print(f"ghost scanner: {len(bars)} symbols scanned ({len(failed)} failed), "
          f"bars through {newest:%Y-%m-%d %H:%M %Z}; {added} new ghost trade(s)")
    for t in trades[len(existing):]:
        print(f"  + {t.symbol:<6} {t.entry_at:%m-%d %H:%M} @ {t.entry:.2f} "
              f"stop {t.stop:.2f} target {t.target:.2f}")
    months = sorted({t.entry_at.strftime("%Y-%m") for t in trades})
    for m in months:
        s = gs.summary(trades, m, last)
        pct = f"{s.mean_pct:+.2f}% per trade" if s.mean_pct is not None else "no price"
        print(f"  {m}: {s.closed} closed ({s.won} won) {s.total_r:+.2f}R, "
              f"{s.open} open {s.open_r:+.2f}R marked; {pct}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
