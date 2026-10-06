"""Diagnostic of the momentum control: where in the ranking the 2016-26 return sits.

    python scripts/momentum_bucket_check.py
    DROP=NVDA,AMD python scripts/momentum_bucket_check.py   # leave names out

READ-ONLY research. Run 2026-10-06 after indicator_screen_test.py showed no
quintile spread for momentum itself; see WORKLIST §31."""
import asyncio, statistics, sys
sys.path.insert(0, "scripts")
from collections import defaultdict
from datetime import date
import httpx
from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500
from momentum_guard_test import union_membership, window
from momentum_hac_test import newey_west_t
LO, HI, HOLD = date(2015, 6, 1), date(2026, 10, 2), 63
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        hs = [await index_history.load(c, spec=s) for s in (SP500, SP400)]
        syms = tuple(sorted(set().union(*(h.symbols_ever(LO, HI, collapse_share_classes=True) for h in hs))))
        bench = window(await fetch_bars("^GSPC", c, range_="12y"), LO, HI)
        raw, _ = await fetch_many(syms, c, range_="12y")
    mem0 = union_membership(hs)
    import os
    drop = set(os.environ.get("DROP", "").split(",")) - {""}
    mem = lambda d: mem0(d) - drop
    bars = {s: w for s, b in raw.items() if (w := window(b, LO, HI)) is not None and len(w) > 273}
    ix = {s: {d: i for i, d in enumerate(b.dates)} for s, b in bars.items()}
    ends = [i for i in range(len(bench.dates) - 1) if bench.dates[i + 1].month != bench.dates[i].month
            and bench.dates[i] >= date(2016, 6, 1) and i + 1 + HOLD < len(bench.dates)]
    out = defaultdict(list); halves = defaultdict(lambda: defaultdict(list)); names = defaultdict(int)
    for e in ends:
        w, en, ex = bench.dates[e], bench.dates[e + 1], bench.dates[e + 1 + HOLD]
        rows = []
        for s in mem(w):
            j = ix.get(s)
            if not j or w not in j or en not in j or ex not in j or j[w] < 273: continue
            cl = bars[s].closes
            rows.append((cl[j[w] - 21] / cl[j[w] - 252] - 1, cl[j[ex]] / cl[j[en]] - 1, s))
        if len(rows) < 200: continue
        m = statistics.fmean(r[1] for r in rows)
        rows.sort(key=lambda r: -r[0]); n = len(rows)
        cuts = {"top 10": rows[:10], "top 11-30": rows[10:30], "top decile": rows[:n // 10],
                "Q5 (top 20%)": rows[:n // 5], "Q4": rows[n // 5:2 * n // 5], "Q3": rows[2 * n // 5:3 * n // 5],
                "Q2": rows[3 * n // 5:4 * n // 5], "Q1 (bottom)": rows[4 * n // 5:]}
        for k, g in cuts.items():
            v = statistics.fmean(r[1] for r in g) - m
            out[k].append(v); halves[k][w.year <= 2020].append(v)
        for r in rows[:10]: names[r[2]] += 1
    print(f"{len(ends)} month-ends; 63-day return minus universe average, per month\n")
    for k, v in out.items():
        print(f"   {k:<14}{statistics.fmean(v) * 100:+7.2f}pp  NW t {newey_west_t(v, 2):5.2f}"
              f"   2016-20 {statistics.fmean(halves[k][True]) * 100:+6.2f}   2021-26 {statistics.fmean(halves[k][False]) * 100:+6.2f}")
    print("\n   most frequent top-10 names:", ", ".join(f"{s} {c}" for s, c in sorted(names.items(), key=lambda x: -x[1])[:15]))
asyncio.run(main())
