"""The core: hold the S&P 500 above its 10-month average, T-bills below it.

    python scripts/core_trend_test.py

READ-ONLY research. Changes nothing live.

WHY THIS, AND WHY IT CAN BE TRUSTED MORE THAN ANYTHING FOUND HERE
Faber, "A Quantitative Approach to Tactical Asset Allocation" (2007): at each
month-end, hold the index if its close is above the 10-month simple average,
otherwise cash. Its claim was never higher return -- it was equity-like return
with far smaller drawdowns. Published in 2007, so 2007-2026 is genuinely
out of sample: nobody, including this repo, chose it from those years.
Momentum's crash guard (WORKLIST §31, arm A) is the same rule, which is why
it is tested on its own first.

RULES -- FIXED 2026-10-05, COMMITTED BEFORE THIS SCRIPT RAN
    asset     SPY, Yahoo adjusted closes (dividends included), daily, 30y
    cash      13-week T-bill (^IRX), daily accrual at the prior day's yield
    signal    last trading day of each month: SPY close vs the mean of the
              last 10 month-end closes (that one included)
    trade     at the next day's close; 10bps per switch
    compare   buy-and-hold SPY over the same days
JUDGED ON 2007-2026 (post-publication). Expected, and what would count:
  the rule's max drawdown is well under buy-and-hold's AND its annual return
  is within 2 percentage points of buy-and-hold's. Shown too: 1997-2006
  (in-sample for Faber), by year, and every switch.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars

SMA_MONTHS, SWITCH_COST = 10, 0.0010
SPLIT = date(2007, 1, 1)


def month_ends(dates) -> set[int]:
    return {i for i in range(len(dates) - 1) if dates[i + 1].month != dates[i].month}


def simulate(dates, closes, bill_yield):
    """Daily equity of the rule and of buy-and-hold, plus the switch log."""
    ends = sorted(month_ends(dates))
    end_set = set(ends)
    rule, hold = [1.0], [1.0]
    invested, pending = False, None
    switches = []
    seen_ends: list[int] = []
    for i in range(1, len(dates)):
        r_spy = closes[i] / closes[i - 1] - 1
        r_cash = bill_yield[i - 1] / 100 / 252
        hold.append(hold[-1] * (1 + r_spy))
        rule.append(rule[-1] * (1 + (r_spy if invested else r_cash)))
        if pending is not None:                       # act at this close
            if pending != invested:
                rule[-1] *= 1 - SWITCH_COST
                switches.append((dates[i], "IN " if pending else "OUT"))
            invested, pending = pending, None
        if i in end_set:
            seen_ends.append(i)
            if len(seen_ends) >= SMA_MONTHS:
                sma = statistics.fmean(closes[j] for j in seen_ends[-SMA_MONTHS:])
                pending = closes[i] > sma
    return rule, hold, switches


def stats(dates, equity, lo, hi):
    idx = [i for i, d in enumerate(dates) if lo <= d < hi]
    a, b = idx[0], idx[-1]
    years = (dates[b] - dates[a]).days / 365.25
    cagr = (equity[b] / equity[a]) ** (1 / years) - 1
    peak, dd = equity[a], 0.0
    for i in range(a, b + 1):
        peak = max(peak, equity[i])
        dd = max(dd, 1 - equity[i] / peak)
    daily = [equity[i] / equity[i - 1] - 1 for i in range(a + 1, b + 1)]
    vol = statistics.stdev(daily) * 252 ** 0.5
    return cagr * 100, dd * 100, vol * 100


async def main() -> int:
    async with httpx.AsyncClient(timeout=60) as client:
        spy = await fetch_bars("SPY", client, range_="30y")
        irx = await fetch_bars("^IRX", client, range_="30y")
    bill = dict(zip(irx.dates, irx.closes))
    dates, closes, yields, last = [], [], [], 0.0
    for d, c in zip(spy.dates, spy.closes):
        last = bill.get(d, last)
        dates.append(d)
        closes.append(c)
        yields.append(last)
    rule, hold, switches = simulate(dates, closes, yields)
    print(f"SPY {dates[0]}..{dates[-1]}, {len(dates)} days; {len(switches)} switches\n")
    periods = (("1997-2006 (Faber's in-sample)", date(1997, 9, 1), SPLIT),
               ("2007-2026 OUT OF SAMPLE", SPLIT, date(2100, 1, 1)))
    for label, lo, hi in periods:
        rc, rd, rv = stats(dates, rule, lo, hi)
        hc, hd, hv = stats(dates, hold, lo, hi)
        print(f"  {label}")
        print(f"     trend rule    {rc:+6.2f}%/yr   max DD {rd:5.1f}%   vol {rv:4.1f}%")
        print(f"     buy and hold  {hc:+6.2f}%/yr   max DD {hd:5.1f}%   vol {hv:4.1f}%")
    rc, rd, _ = stats(dates, rule, SPLIT, date(2100, 1, 1))
    hc, hd, _ = stats(dates, hold, SPLIT, date(2100, 1, 1))
    ok = rd < hd and rc >= hc - 2
    print(f"\n  VERDICT (2007-2026): drawdown {rd:.1f}% vs {hd:.1f}%, return gap {rc - hc:+.2f}pp/yr "
          f"-> {'PASS' if ok else 'FAIL'}")
    print("\n  by year        rule    hold")
    for y in range(dates[0].year + 1, dates[-1].year + 1):
        idx = [i for i, d in enumerate(dates) if d.year == y]
        if idx:
            a, b = max(idx[0] - 1, 0), idx[-1]
            print(f"   {y}      {(rule[b] / rule[a] - 1) * 100:+6.1f}%  {(hold[b] / hold[a] - 1) * 100:+6.1f}%")
    print("\n  switches since 2007: " + ", ".join(f"{d:%Y-%m} {s}" for d, s in switches if d >= SPLIT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
