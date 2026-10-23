"""WORKLIST §64. Read-only: stop-only vs 'sell when the trend breaks' (close < 20/50-day average),
on strong-momentum S&P 500 names, 5y daily bars, entries every 5th day at the close.
Stop 2.5 x ATR14 (gap through fills at the open); max 60 sessions; 0.10% cost in R."""
import json, statistics, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scanner.features.bars import parse_chart
sp = {l.split(",")[0].strip() for l in Path("data/constituents.csv").read_text().splitlines()[1:]}
res = {"A stop only": [], "B stop or close < 20-day avg": [], "C stop or close < 50-day avg": []}
held = {k: [] for k in res}
half = {k: ([], []) for k in res}
for f in Path("data/bars").glob("*__5y__1d.json"):
    s = f.name.split("__")[0]
    if s not in sp: continue
    try: b = parse_chart(json.loads(f.read_text())["payload"], s)
    except Exception: continue
    o, h, l, c = b.opens, b.highs, b.lows, b.closes
    n = len(c)
    if n < 320: continue
    tr = [0] + [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, n)]
    for i in range(260, n - 61, 5):
        if c[i] / c[i-252] - 1 < 0.40: continue                      # strong momentum only
        if c[i] < sum(c[i-49:i+1]) / 50: continue                     # enter in an uptrend
        e = c[i]; risk = 2.5 * sum(tr[i-13:i+1]) / 14; stop = e - risk
        for name, ma in (("A stop only", None), ("B stop or close < 20-day avg", 20), ("C stop or close < 50-day avg", 50)):
            out = None; days = 60
            for j in range(i+1, i+61):
                if o[j] <= stop: out = o[j]; days = j - i; break
                if l[j] <= stop: out = stop; days = j - i; break
                if ma and c[j] < sum(c[j-ma+1:j+1]) / ma: out = c[j]; days = j - i; break
            if out is None: out = c[i+60]
            r = (out - e) / risk - 0.001 * e / risk
            res[name].append(r); held[name].append(days)
            half[name][0 if i < n // 2 + 130 else 1].append(r)
print("strong-momentum S&P 500 entries (12m > +40%, above the 50-day avg), 5 years\n")
for k, rs in res.items():
    rs_s = sorted(rs)
    print(f"{k:32s} n={len(rs):5d}  avg {statistics.fmean(rs):+.3f}R  win {sum(r>0 for r in rs)/len(rs):.0%}  "
          f"worst 5% {rs_s[len(rs)//20]:+.2f}R  days held {statistics.fmean(held[k]):4.1f}  "
          f"halves {statistics.fmean(half[k][0]):+.2f} / {statistics.fmean(half[k][1]):+.2f}")
