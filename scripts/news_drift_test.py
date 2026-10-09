"""Big moves WITH news keep going; big moves WITHOUT news reverse? (WORKLIST §57)

    python scripts/news_drift_test.py

READ-ONLY research. The published claim (Chan, JFE 2003, "Stock price reaction
to news and no-news"): after a large move, stocks WITH a public headline drift
the same way for weeks; stocks with NO headline reverse. Prompted by SNPS on
2026-09-30/10-01: a $1B Amazon deal, an OpenAI partnership and an investor day,
+5% then +7.6% at the next open and +4.8% that day.

RULES, FIXED BEFORE THE FIRST RUN
  universe   current S&P 500 members (survivors: flatters all groups alike),
             cached 5-year daily bars.
  period     move days 2024-10-01 .. 2026-09-18 (room for the 10-day hold).
  event      close-to-close move on day D of at least +4% (UP) or -4% (DOWN).
  news       Alpaca (Benzinga) headlines tagged with the symbol, created from
             16:00 ET on D-1 to 16:00 ET on D. NEWS = at least one.
  earnings   D or D-1 is a Nasdaq report date (data/earnings/history.json):
             reported separately -- earnings are news, but a different kind.
  trade      buy the open of D+1 (the morning after: the earliest the desk
             can act), sell the close of D+10.
  measure    excess over the equal-weight average of the whole universe over
             the same window; 0.10% cost.
  prediction UP+news > UP+no-news, DOWN+news < DOWN+no-news, and no-news moves
             reverse. Reported for both halves of the period.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.features.bars import parse_chart

START, END = date(2024, 10, 1), date(2026, 9, 18)
MOVE = 4.0
HOLD = 10
COST = 0.10
CACHE = Path("data/news_drift")
EARNINGS = Path("data/earnings/history.json")
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
SPLIT = date(2025, 10, 1)


def load_prices():
    sp = {l.split(",")[0].strip() for l in Path("data/constituents.csv").read_text().splitlines()[1:]}
    out = {}
    for f in Path("data/bars").glob("*__5y__1d.json"):
        s = f.name.split("__")[0]
        if s not in sp:
            continue
        try:
            b = parse_chart(json.loads(f.read_text())["payload"], s)
        except Exception:                                  # noqa: BLE001
            continue
        if len(b.closes) > 300:
            out[s] = (list(b.dates), list(b.opens), list(b.closes))
    return out


async def news_counts(day: date, symbols: list[str], client) -> dict[str, int]:
    path = CACHE / f"{day.isoformat()}.json"
    if path.exists():
        return json.loads(path.read_text())
    # 16:00 ET is 20:00 UTC in summer, 21:00 in winter; 20:30 splits the
    # difference by at most 30 minutes on either side of the close.
    start = datetime.combine(day - timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=20, minutes=30)
    end = start + timedelta(days=1)
    if day.weekday() == 0:                                 # Monday: news since Friday's close
        start -= timedelta(days=2)
    counts: dict[str, int] = defaultdict(int)
    headers = {"APCA-API-KEY-ID": settings.alpaca_key_id,
               "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    for k in range(0, len(symbols), 40):
        chunk = symbols[k:k + 40]
        token = None
        while True:
            params = {"symbols": ",".join(chunk), "start": start.isoformat().replace("+00:00", "Z"),
                      "end": end.isoformat().replace("+00:00", "Z"), "limit": 50}
            if token:
                params["page_token"] = token
            for attempt in range(4):
                try:
                    r = await client.get(NEWS_URL, params=params, headers=headers, timeout=30)
                    if r.status_code == 429:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    body = r.json()
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(3)
            else:
                raise RuntimeError(f"news unavailable for {day}")
            for item in body.get("news", []):
                for s in item.get("symbols", []):
                    if s in chunk:
                        counts[s] += 1
            token = body.get("next_page_token")
            if not token:
                break
            await asyncio.sleep(0.35)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts))
    return counts


def line(name: str, xs: list[float]) -> None:
    if len(xs) < 2:
        print(f"  {name:34s} n={len(xs)}")
        return
    m, sd = statistics.fmean(xs), statistics.pstdev(xs) or 1
    print(f"  {name:34s} n={len(xs):5d}  excess {m:+.2f}%  t {m / sd * len(xs) ** .5:+.1f}  "
          f"beat the market {sum(1 for x in xs if x > 0) / len(xs):.0%}")


async def main() -> int:
    prices = load_prices()
    idx = {s: {d: i for i, d in enumerate(p[0])} for s, p in prices.items()}
    report_days = defaultdict(set)
    if EARNINGS.exists():
        for day, rows in json.loads(EARNINGS.read_text()).items():
            for sym, _ in rows:
                report_days[sym].add(date.fromisoformat(day))
    # universe equal-weight window return, by start-date index of a reference series
    ref = max(prices.values(), key=lambda p: len(p[0]))[0]
    events = []                                            # (day, sym, move%, excess%)
    window_mean: dict[date, float] = {}
    for d0 in ref:
        if not (START <= d0 <= END):
            continue
        rets = []
        for s, (dates, o, c) in prices.items():
            i = idx[s].get(d0)
            if i is None or i + HOLD >= len(c):
                continue
            rets.append(c[i + HOLD] / o[i + 1] - 1)
        if rets:
            window_mean[d0] = statistics.fmean(rets)
    for s, (dates, o, c) in prices.items():
        for i in range(1, len(c) - HOLD - 1):
            d0 = dates[i]
            if d0 not in window_mean:
                continue
            move = (c[i] / c[i - 1] - 1) * 100
            if abs(move) >= MOVE:
                ex = ((c[i + HOLD] / o[i + 1] - 1) - window_mean[d0]) * 100 - COST
                events.append((d0, s, move, ex))
    by_day = defaultdict(list)
    for d0, s, *_ in events:
        by_day[d0].append(s)
    print(f"{len(events):,} moves of {MOVE}%+ on {len(by_day)} days; fetching news (cached)...")
    news = {}
    async with httpx.AsyncClient() as client:
        for n, (d0, syms) in enumerate(sorted(by_day.items()), 1):
            news[d0] = await news_counts(d0, sorted(set(syms)), client)
            if n % 50 == 0:
                print(f"  {n}/{len(by_day)} days")
    groups = defaultdict(list)
    for d0, s, move, ex in events:
        side = "UP" if move > 0 else "DOWN"
        earn = d0 in report_days[s] or (d0 - timedelta(days=1)) in report_days[s] or \
            (d0.weekday() == 0 and (d0 - timedelta(days=3)) in report_days[s])
        kind = "earnings" if earn else ("news" if news[d0].get(s, 0) > 0 else "no news")
        half = "1st" if d0 < SPLIT else "2nd"
        groups[(side, kind)].append(ex)
        groups[(side, kind, half)].append(ex)
    print(f"\nexcess over the market, buy the next open, hold {HOLD} sessions, {COST}% cost\n")
    for side in ("UP", "DOWN"):
        for kind in ("no news", "news", "earnings"):
            line(f"{side:4s} {kind}", groups[(side, kind)])
            for half in ("1st", "2nd"):
                line(f"       {half} half", groups[(side, kind, half)])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
