"""The scan watchlist: what the scanner is looking at, for TradingAgents to rate.

The live scan sees ~100 setups a run and trades only a fresh one (1-2 a day),
so the premarket TradingAgents pass used to have nothing but yesterday's slate.
This runs the SAME screen, read-only, before the open and keeps the setups
that are closest to tradeable:

  - triggered in the last 5 sessions (the sweep forms only 3-6 times a day
    across the S&P 500, so one session alone left 1 name on 2026-10-06)
  - still live: price between its stop and its target
  - the live gates: price >= $5, 20-bar dollar volume >= $20M per bar
    (stop >= 1.5% and reward:risk >= 1.5 are enforced by scan() itself)
  - ranked by smc.priority, the scan's own deterministic score

After the scan's names come the operator's watchlist (watchlists/kraken.txt):
names the operator follows, rated so the morning report covers them too.

Record-only like everything TradingAgents does: the list only decides which
names get a rating. Saved to data/advisor/scan_watch.json for the live passes.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

WATCH = Path("data/advisor/scan_watch.json")
OPERATOR_LIST = Path("watchlists/kraken.txt")
MIN_PRICE, MIN_DOLLAR_VOLUME = 5.0, 20e6


SESSIONS = 5


def operator_names(path: Path = OPERATOR_LIST) -> tuple[str, ...]:
    """Tickers from the operator's list: one per line, '#' starts a comment."""
    if not path.exists():
        return ()
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip().upper()
        if name and name not in out:
            out.append(name)
    return tuple(out)


def select(rows: Iterable[tuple[str, date, bool, float, float, float]], *,
           sessions: set[date], limit: int) -> tuple[str, ...]:
    """rows: (symbol, trigger date, still live, price, $vol per bar, priority)."""
    keep = [(prio, sym) for sym, trig, live, price, dvol, prio in rows
            if trig in sessions and live and price >= MIN_PRICE and dvol >= MIN_DOLLAR_VOLUME]
    keep.sort(key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    for _, sym in keep:
        if sym not in out:
            out.append(sym)
    return tuple(out[:limit])


async def build(client, *, limit: int = 10) -> tuple[str, ...]:
    """Run the live screen over the S&P 500 and save the ranked watchlist."""
    import asyncio

    from scanner.execution.policy import TradingPolicy
    from scanner.features.intraday import drop_incomplete, fetch_intraday, max_range
    from scanner.screens.smc import is_live_setup, priority, scan
    from scanner.sources import composite
    from scanner.sources.index_history import SP500

    v = TradingPolicy()
    members = await composite.load_universe(client, (SP500,))
    symbols = tuple(sorted(m.symbol for m in members))
    loaded = await asyncio.gather(*(fetch_intraday(s, client, minutes=v.interval_minutes,
                                                   range_=max_range(v.interval_minutes))
                                    for s in symbols), return_exceptions=True)
    rows, days = [], set()
    for sym, bars in zip(symbols, loaded):
        if isinstance(bars, Exception) or not bars.closes:
            continue
        bars = drop_incomplete(bars, minutes=v.interval_minutes)
        if len(bars) < 60:
            continue
        days.update(t.date() for t in bars.times[-(13 * SESSIONS + 13):])
        dvol = statistics.fmean(c * vol for c, vol in zip(bars.closes[-20:], bars.volumes[-20:]))
        for setup in scan(bars, k=v.swing_k, min_reward_risk=v.min_reward_risk,
                          min_risk_pct=v.min_risk_pct, require_value_sweep=v.require_value_sweep):
            rows.append((sym, bars.times[setup.trigger_index].date(), is_live_setup(bars, setup),
                         bars.closes[-1], dvol, priority(setup)))
    recent = set(sorted(days)[-SESSIONS:])
    from_scan = select(rows, sessions=recent, limit=limit) if recent else ()
    chosen = tuple(dict.fromkeys((*from_scan, *operator_names())))
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    WATCH.write_text(json.dumps({"made": datetime.now().astimezone().isoformat(),
                                 "sessions": sorted(d.isoformat() for d in recent),
                                 "from_scan": list(from_scan), "names": list(chosen)}),
                     encoding="utf-8")
    return chosen


def saved() -> tuple[str, ...]:
    """Today's list for the live passes (empty if none was built)."""
    if not WATCH.exists():
        return ()
    try:
        return tuple(json.loads(WATCH.read_text(encoding="utf-8")).get("names", []))
    except ValueError:
        return ()
