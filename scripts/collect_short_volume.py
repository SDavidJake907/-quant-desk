"""FINRA daily short-sale volume for the S&P 500, from 2018-08-01. Resumable.

    python scripts/collect_short_volume.py
    python scripts/collect_short_volume.py --start 2024-01-01

READ-ONLY against FINRA's public CDN. Writes data/short_volume/YYYY.csv.

WHAT IT IS
FINRA's Consolidated NMS file: per symbol per day, the volume of short sales
reported to its trade reporting facilities during market hours, and the total
volume reported there. Posted by 18:00 ET the same day, free, no key; the
earliest consolidated file is 2018-08-01 -- which lines up with the unseen
2018 bar store. It is short-sale VOLUME (trading flow), not short INTEREST
(positions), and it covers off-exchange reporting, so short_ratio is a
relative measure across names and days, not an absolute share of all trading.

    date, symbol, short_volume, short_exempt, total_volume, short_ratio
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

URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{:%Y%m%d}.txt"
OUT = Path("data/short_volume")
HEADERS = {"User-Agent": "stock-scanner research Kittickds@icloud.com"}
CONCURRENCY = 4


def universe() -> set[str]:
    with open("data/constituents.csv", encoding="utf-8") as handle:
        return {r["Symbol"].strip() for r in csv.DictReader(handle)}


def have() -> set[str]:
    days = set()
    for path in OUT.glob("*.csv"):
        with path.open(encoding="utf-8") as handle:
            days.update(row["date"] for row in csv.DictReader(handle))
    return days


async def fetch(client, day: date, wanted: set[str]):
    try:
        r = await client.get(URL.format(day), headers=HEADERS, timeout=60)
    except httpx.HTTPError as exc:
        return day, f"http: {exc}"
    if r.status_code in (403, 404):
        return day, None                      # holiday or not yet posted
    if r.status_code != 200:
        return day, f"HTTP {r.status_code}"
    rows = []
    for line in r.text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5 or parts[1] not in wanted:
            continue
        try:
            short, exempt, total = float(parts[2]), float(parts[3]), float(parts[4])
        except ValueError:
            continue
        rows.append([day.isoformat(), parts[1], round(short), round(exempt), round(total),
                     round(short / total, 4) if total else ""])
    return day, rows


async def main(start: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    wanted, done = universe(), have()
    day, end = date.fromisoformat(start), date.today() - timedelta(days=1)
    todo = []
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in done:
            todo.append(day)
        day += timedelta(days=1)
    print(f"{len(todo)} trading day(s) to fetch for {len(wanted)} symbols "
          f"({len(done)} already stored)", flush=True)
    stored = missing = 0
    async with httpx.AsyncClient() as client:
        for k in range(0, len(todo), CONCURRENCY):
            results = await asyncio.gather(*(fetch(client, d, wanted) for d in todo[k:k + CONCURRENCY]))
            by_year: dict[int, list] = {}
            for d, rows in results:
                if rows is None:
                    missing += 1
                elif isinstance(rows, str):
                    print(f"  ! {d}: {rows}")
                else:
                    by_year.setdefault(d.year, []).extend(rows)
                    stored += 1
            for year, rows in by_year.items():
                path = OUT / f"{year}.csv"
                new = not path.exists()
                with path.open("a", newline="", encoding="utf-8") as handle:
                    w = csv.writer(handle)
                    if new:
                        w.writerow(["date", "symbol", "short_volume", "short_exempt",
                                    "total_volume", "short_ratio"])
                    w.writerows(rows)
            if (k // CONCURRENCY) % 50 == 0:
                print(f"  {k + len(results)}/{len(todo)} days, {stored} stored", flush=True)
            await asyncio.sleep(0.2)
    print(f"done: {stored} day(s) stored, {missing} holiday/unposted -> {OUT}/")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2018-08-01")
    raise SystemExit(asyncio.run(main(p.parse_args().start)))
