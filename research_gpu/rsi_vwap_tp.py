"""TRIALS #74: take profit when 5-min RSI(14) > 75 AND price > 1% above VWAP.

Operator, 2026-10-19, after seeing today's highs (RSI 69-87, 0.7-1.3% above
VWAP): "use this as a Tp, and on all the stocks ... see where it comes at on
each postion". The thresholds come from ONE day, so this is an out-of-sample
check of them on two years.

Data: Alpaca SIP 5-min bars, regular session, 2024-10-01 -> 2026-10-16, cached in
data/research_5m/<SYM>.json. Names: her 7 (COHR MPC MU NVDA SNDK VLO WDC) + 100
random S&P names from the 15m cache (seed 74).

Rule, per stock, per day, starting the day held (as the books are):
  RSI(14) Wilder on 5-min closes, carried across days; VWAP resets each day.
  The first 5-min bar CLOSE with RSI > 75 and close > VWAP x 1.01 -> sell at the
  NEXT bar's open (the signal is only known when the bar closes). Buy back at
  the day's last close. If the signal is on the last bar: no trade.
  Cost 0.05% per side. Hold never sells. Score: rule minus hold, per day.
  Secondary (optimistic): sell at the signal bar's own close.

PRIMARY: next-open fill, the 100 random names. PASS = mean daily (rule - hold)
across names > 0, t >= 2, both halves > 0. Secondary needs |t| >= 3.3.
Her 7 are shown per stock (where it fires, how far up from the open, what it
saved or cost) -- not graded: picked with hindsight, 7 names.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
AK = ZoneInfo("America/Anchorage")
OUT = Path("data/research_5m")
START, END = "2024-10-01T00:00:00Z", "2026-10-17T00:00:00Z"
RSI_N, RSI_MIN, VWAP_MIN = 14, 75.0, 0.01
COST = 0.0005
HERS = ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC")
N_RANDOM, SEED = 100, 74


# ---- data -------------------------------------------------------------------
async def fetch(client, sym: str, headers) -> list:
    path = OUT / f"{sym}.json"
    if path.exists():
        return json.loads(path.read_text())
    bars, token = [], None
    while True:
        p = {"timeframe": "5Min", "start": START, "end": END, "feed": "sip",
             "limit": 10000, "adjustment": "split"}
        if token:
            p["page_token"] = token
        for attempt in range(5):
            r = await client.get(f"https://data.alpaca.markets/v2/stocks/{sym}/bars",
                                 params=p, headers=headers)
            if r.status_code == 429:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        j = r.json()
        bars += [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in j.get("bars") or []]
        token = j.get("next_page_token")
        if not token:
            break
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bars))
    return bars


async def fetch_all(syms) -> dict:
    import httpx
    from scanner.config import settings
    h = {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    out = {}
    async with httpx.AsyncClient(timeout=60) as c:
        sem = asyncio.Semaphore(4)

        async def one(s):
            async with sem:
                out[s] = await fetch(c, s, h)
        await asyncio.gather(*(one(s) for s in syms))
    return out


# ---- the rule ---------------------------------------------------------------
def sessions(raw) -> list[tuple]:
    days = defaultdict(list)
    for t, o, h, l, c, v in raw:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
        if time(9, 30) <= dt.time() < time(16, 0):
            days[dt.date()].append((dt, o, h, l, c, v))
    return [(d, b) for d, b in sorted(days.items()) if len(b) >= 70]


def run_stock(days) -> list[dict]:
    """One record per day: diffs for both fills, plus where the signal fired."""
    out = []
    ag = al = 0.0
    prev_c = None
    k = 0
    for di, (day, bars) in enumerate(days):
        pv = vol = 0.0
        sig = None
        rsis = []
        for i, (dt, o, h, l, c, v) in enumerate(bars):
            if prev_c is not None:
                d = c - prev_c
                g, ls = max(d, 0.0), max(-d, 0.0)
                k += 1
                if k <= RSI_N:
                    ag += g / RSI_N
                    al += ls / RSI_N
                else:
                    ag = (ag * (RSI_N - 1) + g) / RSI_N
                    al = (al * (RSI_N - 1) + ls) / RSI_N
            prev_c = c
            r = 100 - 100 / (1 + ag / al) if al else 100.0
            pv += (h + l + c) / 3 * v
            vol += v
            vw = pv / vol if vol else c
            if sig is None and k > RSI_N and r > RSI_MIN and c > vw * (1 + VWAP_MIN) \
                    and i < len(bars) - 1:
                sig = i
        if di == 0:
            continue
        pc = days[di - 1][1][-1][4]
        close = bars[-1][4]
        hold = close / pc - 1
        rec = {"day": day, "hold": hold, "next": 0.0, "same": 0.0, "fired": sig is not None}
        if sig is not None:
            nxt = bars[sig + 1][1]
            same = bars[sig][4]
            rec["next"] = (nxt - close) / pc - 2 * COST
            rec["same"] = (same - close) / pc - 2 * COST
            rec["at"] = bars[sig + 1][0].astimezone(AK).strftime("%H:%M")
            rec["up_from_open"] = nxt / bars[0][1] - 1
            rec["day_high_after"] = max(b[2] for b in bars[sig + 1:]) / nxt - 1
        out.append(rec)
    return out


def stats(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd else 0.0)


def pooled(per: dict, key: str):
    by_day = defaultdict(list)
    for recs in per.values():
        for r in recs:
            by_day[r["day"]].append(r[key])
    s = [sum(v) / len(v) for _, v in sorted(by_day.items())]
    h = len(s) // 2
    m, t = stats(s)
    return m, t, stats(s[:h])[0], stats(s[h:])[0], len(s)


def main() -> int:
    pool = sorted(p.name.split("__")[0] for p in Path("data/intraday_alpaca").glob("*__15m__session.json"))
    rand = random.Random(SEED).sample([s for s in pool if s not in HERS], N_RANDOM)
    raw = asyncio.run(fetch_all([*HERS, *rand]))
    per = {s: run_stock(sessions(raw[s])) for s in raw if raw[s]}
    rnd = {s: per[s] for s in rand if s in per}

    print(f"random names: {len(rnd)}")
    for key, label in (("next", "sell next bar open (PRIMARY)"), ("same", "sell signal bar close")):
        m, t, h1, h2, n = pooled(rnd, key)
        print(f"  {label:<30} rule-hold {m*100:+.3f}%/day t {t:+.2f} halves {h1*100:+.3f}% "
              f"{h2*100:+.3f}%  ({m*25200:+.1f}%/yr) over {n} days")
    fires = sum(r["fired"] for v in rnd.values() for r in v) / sum(len(v) for v in rnd.values())
    print(f"  fires on {fires*100:.0f}% of stock-days")

    print(f"\nher 7, per stock (sell next bar open):")
    print(f"{'sym':<5} {'days':>4} {'fires':>6} {'usual time AK':>13} {'up from open':>12} "
          f"{'ran on after':>12} {'saved/day fired':>15} {'win%':>5} {'per yr vs hold':>14}")
    for s in HERS:
        recs = per.get(s, [])
        f = [r for r in recs if r["fired"]]
        if not f:
            print(f"{s:<5} {len(recs):>4}  never fires")
            continue
        times = sorted(r["at"] for r in f)
        ups = sorted(r["up_from_open"] for r in f)
        runs = sorted(r["day_high_after"] for r in f)
        per_fire = sum(r["next"] for r in f) / len(f)
        win = sum(r["next"] > 0 for r in f) / len(f)
        yr = sum(r["next"] for r in recs) / len(recs) * 252
        print(f"{s:<5} {len(recs):>4} {len(f)/len(recs)*100:5.0f}% {times[len(times)//2]:>13} "
              f"{ups[len(ups)//2]*100:+11.2f}% {runs[len(runs)//2]*100:+11.2f}% "
              f"{per_fire*100:+14.3f}% {win*100:4.0f}% {yr*100:+13.1f}%")
    m, t, h1, h2, _ = pooled(rnd, "next")
    print(f"\nPRIMARY: {'PASS' if m > 0 and t >= 2 and h1 > 0 and h2 > 0 else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
