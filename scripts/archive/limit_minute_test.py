"""The right entry limit, measured on 1-minute bars from when the order really arrives.

    python scripts/limit_minute_test.py               # setups since 2026-03-17
    python scripts/limit_minute_test.py --since 2025-09-15

READ-ONLY research. Changes nothing live. Alpaca SIP 1-minute bars (free tier:
anything older than 15 minutes).

WHY MINUTES
limit_offset_test.py on 30m bars said a limit at the signal price fills 97%
and any offset ~100% -- but live, 3 of 18 never filled. The order reaches the
broker ~3.5 minutes after the trigger bar closes, and by then META, LULU and
SLB were already 0.35-0.66% above the limit. A 30m bar cannot say whether its
low came before or after the order arrived; minutes can.

MODEL
    arrival   trigger bar close + 3.5 minutes (live: :03 scan, ~30s to send)
    limit     signal close x (1 + k), k in 0 .. 0.75%;  "market" = the first
              minute's open after arrival, for comparison
    fill      a later minute trades THROUGH the limit (low < limit) within
              60 minutes (fresh_bars x 30, then cancel_stale_entries); fill at
              min(minute open, limit)
    outcome   from the fill, the setup's own stop and target on the 30m bars,
              Friday flatten, 60-bar cap, 10bps; R against the ORIGINAL risk
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings

from gemma_graded import collect, gates_for

URL = "https://data.alpaca.markets/v2/stocks/{}/bars"
KS = (0.0, 0.0010, 0.0020, 0.0030, 0.0050, 0.0075)
COST = 0.0010


async def minutes(client, symbol, start, end):
    params = {"timeframe": "1Min", "start": start.isoformat().replace("+00:00", "Z"),
              "end": end.isoformat().replace("+00:00", "Z"), "feed": "sip",
              "adjustment": "all", "limit": 1000}
    headers = {"APCA-API-KEY-ID": settings.alpaca_key_id,
               "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    for _ in range(5):
        r = await client.get(URL.format(symbol), params=params, headers=headers, timeout=60)
        if r.status_code == 429:
            await asyncio.sleep(3)
            continue
        r.raise_for_status()
        return [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")), b["o"], b["l"])
                for b in r.json().get("bars") or []]
    return []


def outcome(bars, s, fill, fill_t):
    """R from a fill at `fill` (time fill_t) under the setup's bracket, 30m bars."""
    from scanner.execution.policy import TradingPolicy
    V = TradingPolicy()
    stop, target, risk = s.stop_price, s.target_price, s.entry_price - s.stop_price
    first = next((i for i, t in enumerate(bars.times) if t + timedelta(minutes=30) > fill_t), None)
    if first is None:
        return None
    last = min(s.trigger_index + V.max_hold_bars, len(bars) - 1)
    for i in range(first, last + 1):
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if i > first and (o <= stop or o >= target):
            return (o - fill) / risk - COST * fill / risk
        if lo <= stop:
            return (stop - fill) / risk - COST * fill / risk
        if h >= target:
            return (target - fill) / risk - COST * fill / risk
        if bars.times[i].weekday() == 4 and (i == last or bars.times[i + 1].date() != bars.times[i].date()):
            return (bars.closes[i] - fill) / risk - COST * fill / risk
    return (bars.closes[last] - fill) / risk - COST * fill / risk


async def main(since: str, arrival_min: float = 3.5, guard_pct: float = 0.0) -> int:
    rows = [r for r in collect(Path("data/intraday_alpaca"), date.fromisoformat(since))
            if all(ok for _, ok, _ in gates_for(r[1].upto(r[2].trigger_index), r[2], None))]
    print(f"{len(rows)} gated setups since {since}; fetching 1-minute bars...", flush=True)
    res = {k: [] for k in KS}
    res["market"] = []
    misses = {k: 0 for k in KS}
    async with httpx.AsyncClient() as client:
        for n, (when, bars, s, _) in enumerate(rows, 1):
            close_t = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) + timedelta(minutes=30)
            arrive = close_t + timedelta(minutes=arrival_min)
            # LATE-SIGNAL GUARD (guards.late_signal): when the setup is first
            # seen a bar late, skip it if the newest completed close is already
            # guard_pct below the entry -- that close is the trigger's next bar.
            if guard_pct and arrival_min >= 30:
                nxt = s.trigger_index + 1
                if nxt < len(bars) and bars.closes[nxt] <= s.entry_price * (1 - guard_pct / 100):
                    res.setdefault("guarded", []).append(1)
                    continue
            mins = [m for m in await minutes(client, s.symbol, close_t, arrive + timedelta(minutes=65))
                    if m[0] >= arrive - timedelta(seconds=30)]
            if not mins:
                continue
            res["market"].append(outcome(bars, s, mins[0][1], mins[0][0]))
            for k in KS:
                limit = s.entry_price * (1 + k)
                hit = next(((t, min(o, limit)) for t, o, lo in mins
                            if t < arrive + timedelta(minutes=60) and lo < limit), None)
                if hit is None:
                    misses[k] += 1
                    continue
                res[k].append(outcome(bars, s, hit[1], hit[0]))
            if n % 50 == 0:
                print(f"  {n}/{len(rows)}", flush=True)
    total = len(res["market"])
    print(f"\n{total} setups with minute data; order arrives {arrival_min} min after the bar closes"
          + (f"; {len(res.get('guarded', []))} skipped by the late-signal guard" if guard_pct and arrival_min >= 30 else ""))
    print(f"  {'entry':<18}{'filled':>8}{'mean R':>9}{'total R':>9}{'win':>6}")
    for k in KS:
        got = [r for r in res[k] if r is not None]
        print(f"  {'limit +' + format(k * 100, '.2f') + '%':<18}{len(got) / total * 100:>7.0f}%"
              f"{statistics.fmean(got):>+9.3f}{sum(got):>+9.1f}{sum(v > 0 for v in got) / len(got) * 100:>5.0f}%")
    got = [r for r in res["market"] if r is not None]
    print(f"  {'market at arrival':<18}{len(got) / total * 100:>7.0f}%{statistics.fmean(got):>+9.3f}"
          f"{sum(got):>+9.1f}{sum(v > 0 for v in got) / len(got) * 100:>5.0f}%")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", default="2026-03-17")
    p.add_argument("--arrival", type=float, default=3.5,
                   help="minutes after the trigger bar closes the order lands; 33.5 = seen a scan late")
    p.add_argument("--guard", type=float, default=0.0,
                   help="late-signal guard %%: skip a late setup already this far below its entry")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.since, a.arrival, a.guard)))
