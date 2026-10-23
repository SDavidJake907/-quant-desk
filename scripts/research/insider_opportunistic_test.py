"""Opportunistic insider buying: do insider purchases that break the insider's
usual calendar pattern predict returns, when routine ones do not?

    python scripts/insider_opportunistic_test.py

READ-ONLY research. Changes nothing live.

PRIOR EVIDENCE: Cohen, Malloy & Pomorski (2012), "Decoding Inside
Information": insiders who trade in the same calendar month year after year
are ROUTINE and their trades carry no information; the rest are OPPORTUNISTIC,
and a portfolio of their trades earns ~0.82%/month, rising for ~6 months with
no reversal. Trial #5 (2026-10-05) tested ANY purchase in the prior 90 days
and found nothing -- it never made this distinction.

RULES -- FIXED 2026-10-06, COMMITTED BEFORE THIS SCRIPT RAN
  data       data/insiders/form4.csv: open-market purchases (P) and sales (S),
             2018 -> 2026-06; point-in-time on FILING date
  insider    (owner name, symbol)
  classify   a purchase whose transaction is in year Y is CLASSIFIABLE if the
             insider traded (P or S) in each of Y-3, Y-2, Y-1. ROUTINE if one
             calendar month holds a trade in all three of those years;
             otherwise OPPORTUNISTIC. Unclassifiable purchases are left out of
             both groups. First classifiable year: 2021.
  events     one per (symbol, filing date). OPP if any purchase that day is
             opportunistic; else ROUT if any is routine
  universe   S&P 500 + MidCap 400 members on the filing date
  entry      close of the 2nd trading day after the filing date
  hold       126 trading days (~6 months, the paper's drift horizon); 25bps
  abnormal   stock return minus ^GSPC over the same days
PASS (decided now), both:
  1  OPP mean abnormal return > 0, monthly cohorts, Newey-West t (lag 5,
     since 126-day holds overlap ~6 monthly cohorts) >= 2
  2  OPP minus ROUT per month, mean > 0
Trial #43 in TRIALS.md. Expect about half the paper's size (McLean & Pontiff).
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from momentum_hac_test import newey_west_t  # noqa: E402

DATA = Path("data/insiders/form4.csv")
ENTRY_DAY, HOLD, COST, LAG = 2, 126, 0.0025, 5


def load() -> tuple[dict, list[dict]]:
    """(trade months per insider by year, purchase rows)."""
    months: dict[tuple[str, str], dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    purchases = []
    with DATA.open(encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            try:
                traded = date.fromisoformat(r["trans_date"])
                filed = date.fromisoformat(r["filing_date"])
            except ValueError:
                continue
            key = (r["owner"].strip().lower(), r["symbol"].strip())
            months[key][traded.year].add(traded.month)
            if r["code"] == "P":
                purchases.append({"key": key, "symbol": key[1], "traded": traded, "filed": filed})
    return months, purchases


def classify(months: dict[int, set[int]], year: int) -> str | None:
    prior = [months.get(year - k, set()) for k in (3, 2, 1)]
    if not all(prior):
        return None
    return "ROUT" if prior[0] & prior[1] & prior[2] else "OPP"


def events(months, purchases) -> list[dict]:
    by_day: dict[tuple[str, date], set[str]] = defaultdict(set)
    for p in purchases:
        label = classify(months[p["key"]], p["traded"].year)
        if label:
            by_day[(p["symbol"], p["filed"])].add(label)
    return [{"symbol": s, "filed": d, "group": "OPP" if "OPP" in labels else "ROUT"}
            for (s, d), labels in by_day.items()]


async def main() -> int:
    months, purchases = load()
    evs = events(months, purchases)
    async with httpx.AsyncClient(timeout=60) as client:
        h500 = await index_history.load(client, spec=SP500)
        h400 = await index_history.load(client, spec=SP400)
        evs = [e for e in evs if e["symbol"] in (h500.members_on(e["filed"], collapse_share_classes=True)
                                                 | h400.members_on(e["filed"], collapse_share_classes=True))]
        symbols = tuple(sorted({e["symbol"].replace(".", "-") for e in evs}))
        bench = await fetch_bars("^GSPC", client, range_="12y")
        bars, errors = await fetch_many(symbols, client, range_="12y")
    bi = {d: i for i, d in enumerate(bench.dates)}
    rows = []
    for e in evs:
        b = bars.get(e["symbol"].replace(".", "-"))
        if b is None:
            continue
        i0 = next((i for i, d in enumerate(b.dates) if d > e["filed"]), None)
        if i0 is None or i0 + ENTRY_DAY - 1 + HOLD >= len(b):
            continue
        a, z = i0 + ENTRY_DAY - 1, i0 + ENTRY_DAY - 1 + HOLD          # 2nd session after filing
        ja, jz = bi.get(b.dates[a]), bi.get(b.dates[z])
        if ja is None or jz is None:
            continue
        ret = b.closes[z] / b.closes[a] - 1 - COST
        abn = ret - (bench.closes[jz] / bench.closes[ja] - 1)
        rows.append({**e, "entry": b.dates[a], "abn": abn * 100})
    print(f"{len(purchases)} purchases; {len(evs)} classifiable events in S&P 500+400; "
          f"{len(rows)} priced with a full {HOLD}-day hold; {len(errors)} fetch errors")
    month = lambda r: (r["entry"].year, r["entry"].month)
    by = {g: defaultdict(list) for g in ("OPP", "ROUT")}
    for r in rows:
        by[r["group"]][month(r)].append(r["abn"])
    for g in ("OPP", "ROUT"):
        flat = [x for v in by[g].values() for x in v]
        series = [statistics.fmean(by[g][m]) for m in sorted(by[g])]
        print(f"   {g:<5} events {len(flat):>5}  mean {HOLD}d abnormal {statistics.fmean(flat):+6.2f}%  "
              f"win {sum(x > 0 for x in flat) / len(flat) * 100:.0f}%  per-month NW t {newey_west_t(series, LAG):5.2f}"
              f"  ({len(series)} months)")
    opp_series = [statistics.fmean(by["OPP"][m]) for m in sorted(by["OPP"])]
    both = sorted(set(by["OPP"]) & set(by["ROUT"]))
    gap = [statistics.fmean(by["OPP"][m]) - statistics.fmean(by["ROUT"][m]) for m in both]
    t = newey_west_t(opp_series, LAG)
    print(f"   OPP minus ROUT, per month: {statistics.fmean(gap):+.2f}pp over {len(gap)} months "
          f"(NW t {newey_west_t(gap, LAG):.2f})")
    checks = (statistics.fmean(opp_series) > 0 and t >= 2, statistics.fmean(gap) > 0)
    print(f"   PASS 1 OPP abnormal > 0, t >= 2: {'ok' if checks[0] else 'X'}   "
          f"2 OPP > ROUT: {'ok' if checks[1] else 'X'}   ->  {'PASS' if all(checks) else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
