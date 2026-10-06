"""Historical earnings results (actual EPS, consensus forecast, surprise) by date. Resumable.

    python scripts/collect_earnings.py                 # 2010-01-01 -> yesterday
    python scripts/collect_earnings.py --start 2018-01-01

READ-ONLY against Nasdaq's public earnings calendar (the same undocumented API
sources/calendar.py uses live). Writes data/earnings/YYYY.csv:

    date, symbol, eps, eps_forecast, surprise_pct, n_estimates, time, fiscal_quarter

WHAT TO KNOW BEFORE USING IT
- `date` is the announcement date Nasdaq lists. `time` is usually
  "time-not-supplied", so a backtest must not assume before or after the
  open: measure from the second session after `date`.
- `marketCap` is NOT kept: Nasdaq returns today's value on every past row,
  which is lookahead.
- Coverage thins going back (about 52 companies on a 2010 date against ~290
  in 2026); forecasts are sometimes missing ("" or N/A) and are kept blank.
- One request per weekday, about 1.2s apart, so 2010 onward is ~1.5 hours.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://api.nasdaq.com/api/calendar/earnings"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
OUT = Path("data/earnings")
DONE = OUT / "dates_done.txt"
FIELDS = ["date", "symbol", "eps", "eps_forecast", "surprise_pct", "n_estimates",
          "time", "fiscal_quarter"]
PAUSE, RETRIES = 1.2, 3


def money(text: str | None) -> str:
    """'$1.91' -> '1.91', '($0.12)' -> '-0.12', blanks and N/A -> ''."""
    t = (text or "").strip().replace("$", "").replace(",", "")
    if not t or t.upper() == "N/A":
        return ""
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return str(float(t))
    except ValueError:
        return ""


def plain(text: str | None) -> str:
    t = (text or "").strip().replace(",", "")
    try:
        return str(float(t))
    except ValueError:
        return ""


async def fetch_day(client: httpx.AsyncClient, day: date) -> list[dict] | None:
    for attempt in range(RETRIES):
        try:
            r = await client.get(URL, params={"date": day.isoformat()}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            rows = ((r.json().get("data") or {}).get("rows")) or []
            return [{"date": day.isoformat(), "symbol": (row.get("symbol") or "").strip(),
                     "eps": money(row.get("eps")), "eps_forecast": money(row.get("epsForecast")),
                     "surprise_pct": plain(row.get("surprise")), "n_estimates": plain(row.get("noOfEsts")),
                     "time": (row.get("time") or "").strip(),
                     "fiscal_quarter": (row.get("fiscalQuarterEnding") or "").strip()}
                    for row in rows if row.get("symbol")]
        except (httpx.HTTPError, ValueError) as exc:
            print(f"  {day}: attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(5 * (attempt + 1))
    return None


async def main(start: date) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text(encoding="utf-8").split()) if DONE.exists() else set()
    end = date.today() - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    todo = [d for d in days if d.weekday() < 5 and d.isoformat() not in done]
    print(f"{len(todo)} weekdays to fetch ({len(done)} already done)")
    failed = 0
    async with httpx.AsyncClient() as client:
        for n, day in enumerate(todo, 1):
            rows = await fetch_day(client, day)
            if rows is None:
                failed += 1
                continue
            path = OUT / f"{day.year}.csv"
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                if new:
                    writer.writeheader()
                writer.writerows(rows)
            with DONE.open("a", encoding="utf-8") as handle:
                handle.write(day.isoformat() + "\n")
            if n % 100 == 0:
                print(f"  {n}/{len(todo)}  {day}  {len(rows)} rows")
            await asyncio.sleep(PAUSE)
    print(f"done; {failed} day(s) failed and will be retried on the next run")
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat, default=date(2010, 1, 1))
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.start)))
