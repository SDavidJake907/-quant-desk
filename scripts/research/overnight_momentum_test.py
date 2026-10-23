"""WORKLIST §51. Read-only: where do momentum stocks earn -- overnight or in the session? Weekends?
Current S&P 500 members (survivors), cached 5y daily bars. Each month, top 10% by
12-1 momentum vs everyone; next month's daily returns split into
night (prev close -> open) and day (open -> close); weekend = Fri close -> Mon open."""
import json, math, statistics, sys
from collections import defaultdict
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from scanner.features.bars import parse_chart
sp = {l.split(",")[0].strip() for l in Path("data/constituents.csv").read_text().splitlines()[1:]}
data = {}
for f in Path("data/bars").glob("*__5y__1d.json"):
    s = f.name.split("__")[0]
    if s not in sp: continue
    try: b = parse_chart(json.loads(f.read_text())["payload"], s)
    except Exception: continue
    if len(b.closes) > 300: data[s] = {d: (o, c) for d, o, c in zip(b.dates, b.opens, b.closes)}
dates = sorted({d for v in data.values() for d in v})
months = defaultdict(list)
for d in dates: months[(d.year, d.month)].append(d)
mk = sorted(months)
# per-day log returns pooled by bucket: lists of (night, day, is_weekend_night, weekday)
acc = {"top10": defaultdict(list), "all": defaultdict(list)}
for mi in range(13, len(mk) - 1):
    form = months[mk[mi]][-1]; past = months[mk[mi - 12]][-1]; skip = months[mk[mi - 1]][-1]
    mom = {s: v[skip][1] / v[past][1] - 1 for s, v in data.items() if past in v and skip in v and form in v}
    if len(mom) < 200: continue
    top = set(sorted(mom, key=mom.get)[-len(mom) // 10:])
    days = months[mk[mi + 1]]
    prev = form
    for d in days:
        wk = (d - prev).days > 1 and d.weekday() == 0
        nights, dayr = {"top10": [], "all": []}, {"top10": [], "all": []}
        for s in mom:
            v = data[s]
            if d not in v or prev not in v: continue
            n = math.log(v[d][0] / v[prev][1]); t = math.log(v[d][1] / v[d][0])
            for g in (("top10", "all") if s in top else ("all",)):
                nights[g].append(n); dayr[g].append(t)
        for g in acc:
            if nights[g]:
                acc[g]["night_wkend" if wk else "night_week"].append(statistics.fmean(nights[g]))
                acc[g]["day"].append(statistics.fmean(dayr[g]))
                acc[g][f"wd{d.weekday()}"].append(statistics.fmean(nights[g]) + statistics.fmean(dayr[g]))
                if d.weekday() == 0: acc[g]["mon_close"].append(statistics.fmean(nights[g]) + statistics.fmean(dayr[g]))
        prev = d
yrs = len(acc["all"]["day"]) / 252
def tot(xs): return (math.exp(sum(xs)) - 1) * 100
def t(xs): return statistics.fmean(xs) / statistics.pstdev(xs) * len(xs) ** .5
print(f"{yrs:.1f} years of daily data, top 10% momentum rebuilt monthly\n")
print("bucket                      total  per year   per period   t")
for g in ("top10", "all"):
    a = acc[g]
    for k, lab in (("night_week", "weeknights (close->open)"), ("night_wkend", "weekends (Fri close->Mon open)"),
                   ("day", "sessions (open->close)")):
        xs = a[k]
        print(f"{g:5s} {lab:31s} {tot(xs):+7.1f}% {tot(xs)/yrs:+6.1f}%  {statistics.fmean(xs)*100:+.3f}%  {t(xs):+.1f}")
    print()
print("whole day (night+session) by weekday, average per day:")
for g in ("top10", "all"):
    print(f"  {g:5s} " + "  ".join(f"{n} {statistics.fmean(acc[g][f'wd{i}'])*100:+.3f}%" for i, n in enumerate(("Mon","Tue","Wed","Thu","Fri"))))
print("\nweekday t-stats (top10), and Monday minus the other days:")
a = acc["top10"]
for i, n in enumerate(("Mon","Tue","Wed","Thu","Fri")):
    print(f"  {n} n={len(a[f'wd{i}'])} t {t(a[f'wd{i}']):+.1f}")
others = [x for i in range(1, 5) for x in a[f"wd{i}"]]
m, o = a["wd0"], others
diff = statistics.fmean(m) - statistics.fmean(o)
se = (statistics.pvariance(m)/len(m) + statistics.pvariance(o)/len(o)) ** .5
print(f"  Monday - other days {diff*100:+.3f}%  t {diff/se:+.1f}")
half = len(a["day"]) // 2
for name, k in (("weeknights", "night_week"), ("sessions", "day")):
    xs = a[k]; h = len(xs)//2
    print(f"  {name}: first half {statistics.fmean(xs[:h])*100:+.3f}%/day, second half {statistics.fmean(xs[h:])*100:+.3f}%/day")
