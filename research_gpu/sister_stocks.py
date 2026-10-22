"""TRIALS #78: sister stocks (lead-lag). When a big leader jumps, do its smaller
sisters (suppliers, customers, same-industry peers) follow over the next week?

Operator, 2026-10-21: "maybe we can use like the sister stocks on the main ones
we trade ... make a list and save it, go down the line, test it how it affect
each other".

PRIOR EVIDENCE (not from this repo)
Hou (2007): within an industry, big firms' returns lead small firms'. Cohen &
Frazzini (2008, "Economic Links"): customers' returns predict suppliers' next
month. Both published, so expect them weaker now (McLean & Pontiff).

RULES -- FIXED 2026-10-21 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  links      LINKS below: 9 leaders, 3-5 sisters each, chosen from public
             supplier / customer / same-industry ties before any data was read
  data       Yahoo daily bars, range 10y, benchmark ^GSPC; a sister with no
             data is reported and skipped
  shock      day t: leader close-to-close return minus ^GSPC >= +3% (UP)
             or <= -3% (DOWN)
  response   sister: open t+1 -> close t+5 (5 sessions), minus ^GSPC over the
             same open->close; LIFT = response minus that sister's mean
             response over ALL days (so a sister that always rises earns no
             credit for it)
  pooling    per shock: mean lift of the leader's sisters; per calendar date:
             mean over the leaders shocked that day; t on those date means
  cost       0.10% round trip, taken off the UP lift
PASS (decided now), ALL of:
  1  UP shocks: mean lift after cost > 0 with t >= 2
  2  > 0 after cost in both halves: to 2021-06-30, and from 2021-07-01
  3  at least 4 of the 9 leaders with mean UP lift > 0 (not one pair carrying it)
SECONDARY, reported only: DOWN shocks (a sister to avoid), the "not caught up
yet" filter (sister's day-t move under half the leader's), same-day co-move,
the reverse direction (a sister's shock -> its leader's next 5 days), and every
leader -> sister pair.
Overlapping 5-day windows: t is orientation, the both-halves check is the guard.

READ-ONLY research. Changes nothing live.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import sys
from collections import defaultdict
from datetime import date

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many

#: leader -> {sister: why}. Fixed before the first run.
LINKS: dict[str, dict[str, str]] = {
    "NVDA": {"SMCI": "builds NVDA GPU servers", "AMKR": "chip packaging and test",
             "CRDO": "AI data-center connectivity", "VRT": "power and cooling for GPU racks",
             "CLS": "builds AI servers and switches"},
    "AAPL": {"CRUS": "audio chips, most revenue from Apple", "SWKS": "iPhone RF chips",
             "QRVO": "iPhone RF chips", "JBL": "iPhone assembly"},
    "VLO": {"PBF": "smaller refiner", "DINO": "smaller refiner", "PARR": "smaller refiner",
            "DK": "smaller refiner", "CVI": "smaller refiner"},
    "XOM": {"APA": "smaller oil producer", "DVN": "smaller oil producer",
            "OVV": "smaller oil producer", "MTDR": "smaller oil producer", "SM": "smaller oil producer"},
    "JPM": {"ZION": "regional bank", "KEY": "regional bank", "FHN": "regional bank",
            "WAL": "regional bank", "CMA": "regional bank"},
    "NUE": {"CLF": "steelmaker", "CMC": "steelmaker", "STLD": "steelmaker"},
    "CAT": {"TEX": "construction machinery", "OSK": "heavy vehicles",
            "AGCO": "farm machinery", "MTW": "cranes"},
    "HD": {"TREX": "decking sold at HD", "FBIN": "faucets, doors, cabinets",
           "MHK": "flooring", "BLDR": "building products"},
    "LLY": {"WST": "injection components for GLP-1 pens", "VKTX": "small GLP-1 rival",
            "HIMS": "sells GLP-1 treatments"},
}
SHOCK = 0.03
HOLD = 5
COST = 0.0010
SPLIT = date(2021, 7, 1)


def series(b):
    return {d: (o, c) for d, o, c in zip(b.dates, b.opens, b.closes)}


def daily_abn(s, bench, days):
    """day -> close-to-close return minus the index."""
    out = {}
    for d0, d1 in zip(days, days[1:]):
        if d0 in s and d1 in s and d0 in bench and d1 in bench:
            out[d1] = (s[d1][1] / s[d0][1] - 1) - (bench[d1][1] / bench[d0][1] - 1)
    return out


def fwd_abn(s, bench, days):
    """day t -> open t+1 to close t+HOLD, minus the index."""
    out = {}
    for i, d in enumerate(days[:-HOLD]):
        a, z = days[i + 1], days[i + HOLD]
        if all(x in s and x in bench for x in (a, z)):
            out[d] = (s[z][1] / s[a][0] - 1) - (bench[z][1] / bench[a][0] - 1)
    return out


def tstat(xs):
    if len(xs) < 3:
        return float("nan")
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / sd * math.sqrt(len(xs)) if sd else float("nan")


def summarize(by_date, cost=0.0):
    xs = [v - cost for v in by_date.values()]
    if not xs:
        return "n=0"
    return f"n={len(xs):4} mean {statistics.mean(xs) * 100:+.2f}% t {tstat(xs):4.1f}"


async def main() -> int:
    syms = sorted({*LINKS, *(s for v in LINKS.values() for s in v)})
    async with httpx.AsyncClient(timeout=60) as client:
        bars, errors = await fetch_many(tuple(syms), client, range_="10y")
        spx = await fetch_bars("^GSPC", client, range_="10y")
    bench, days = series(spx), list(spx.dates)
    print(f"^GSPC {days[0]} -> {days[-1]}; missing: {', '.join(errors) or 'none'}\n")
    S = {k: series(b) for k, b in bars.items()}
    D = {k: daily_abn(v, bench, days) for k, v in S.items()}
    F = {k: fwd_abn(v, bench, days) for k, v in S.items()}
    base = {k: statistics.mean(v.values()) for k, v in F.items() if v}

    def lifts(leader, sisters, shock_sign, caught_filter=False):
        """date -> mean lift of the sisters after the leader's shock."""
        out = {}
        for d, r in D.get(leader, {}).items():
            if shock_sign * r < SHOCK:
                continue
            vals = [F[s][d] - base[s] for s in sisters
                    if s in F and d in F[s]
                    and (not caught_filter or abs(D[s].get(d, 0.0)) < abs(r) / 2)]
            if vals:
                out[d] = statistics.mean(vals)
        return out

    def pooled(sign, caught=False):
        per_date = defaultdict(list)
        for leader, sisters in LINKS.items():
            for d, v in lifts(leader, sisters, sign, caught).items():
                per_date[d].append(v)
        return {d: sign * statistics.mean(v) for d, v in per_date.items()}

    up = pooled(+1)
    first = {d: v for d, v in up.items() if d < SPLIT}
    second = {d: v for d, v in up.items() if d >= SPLIT}
    leaders_pos = sum(1 for L, s in LINKS.items()
                      if (x := lifts(L, s, +1)) and statistics.mean(x.values()) > 0)
    m_all = statistics.mean(v - COST for v in up.values())
    t_all = tstat([v - COST for v in up.values()])
    m1 = statistics.mean(v - COST for v in first.values()) if first else float("nan")
    m2 = statistics.mean(v - COST for v in second.values()) if second else float("nan")
    print("PRIMARY: leader UP shock -> sisters' next 5 days vs the S&P (lift, after 0.10% cost)")
    print(f"  all      {summarize(up, COST)}")
    print(f"  to 2021-06 {summarize(first, COST)}")
    print(f"  from 2021-07 {summarize(second, COST)}")
    print(f"  leaders with positive UP lift: {leaders_pos} of {len(LINKS)}")
    ok1, ok2, ok3 = m_all > 0 and t_all >= 2, m1 > 0 and m2 > 0, leaders_pos >= 4
    print(f"  PASS 1 t>=2: {'ok' if ok1 else 'X'}   2 both halves: {'ok' if ok2 else 'X'}   "
          f"3 >=4 leaders: {'ok' if ok3 else 'X'}   ->  {'PASS' if ok1 and ok2 and ok3 else 'FAIL'}\n")

    print("SECONDARY (reported only)")
    print(f"  DOWN shock, sisters fall further (sign-flipped lift): {summarize(pooled(-1))}")
    print(f"  UP shock, only sisters that had NOT caught up on day t: {summarize(pooled(+1, True), COST)}")
    rev = defaultdict(list)
    for L, sisters in LINKS.items():
        for s in sisters:
            for d, r in D.get(s, {}).items():
                if r >= SHOCK and L in F and d in F[L]:
                    rev[d].append(F[L][d] - base[L])
    print(f"  REVERSE: sister UP shock -> leader next 5 days: "
          f"{summarize({d: statistics.mean(v) for d, v in rev.items()})}\n")

    print("BY PAIR: same-day co-move (sister's move per 1% of the leader's), and UP-shock lift")
    print(f"  {'leader':6} {'sister':6} {'co-move':>8} {'UP n':>5} {'UP lift':>8} {'DOWN n':>6} {'DOWN lift':>9}  why")
    for L, sisters in LINKS.items():
        for s, why in sisters.items():
            common = [d for d in D.get(L, {}) if d in D.get(s, {})]
            if len(common) < 2:
                print(f"  {L:6} {s:6} {'no data':>8}")
                continue
            x = [D[L][d] for d in common]
            y = [D[s][d] for d in common]
            mx, my = statistics.mean(x), statistics.mean(y)
            vx = sum((a - mx) ** 2 for a in x)
            beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / vx if vx else float("nan")
            u, dn = lifts(L, {s: why}, +1), lifts(L, {s: why}, -1)
            mu = statistics.mean(u.values()) * 100 if u else float("nan")
            md = statistics.mean(dn.values()) * 100 if dn else float("nan")
            print(f"  {L:6} {s:6} {beta:8.2f} {len(u):5} {mu:+7.2f}% {len(dn):6} {md:+8.2f}%  {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
