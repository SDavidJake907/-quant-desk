"""Three published momentum upgrades, tested as one family against the engine.

    python scripts/momentum_family_test.py

READ-ONLY research. Changes nothing live. Trials in TRIALS.md: one family.

THE ENGINE (the baseline every variant must beat): 12-1 momentum, top 10,
equal weight, 63-day books started every month-end (three overlapping
tranches), cash when the S&P 500 closed below its 210-day average. 25bps per
book. Cash earns nothing here (conservative to every arm alike).

VARIANTS -- FIXED 2026-10-06, COMMITTED BEFORE THIS SCRIPT RAN
  vol        Barroso & Santa-Clara (2015) adapted to a LONG-ONLY, CASH-ONLY
             book: exposure = min(1, 15% / annualised volatility of an equal-
             weight portfolio of this month's picks over the prior 126 days);
             rest in cash. Never above 100% (no leverage). The papers studied
             long-short momentum, whose crashes come mostly from the short leg,
             so a weaker effect is expected here.
  residual   Blitz, Huij & Martens (2011): regress each stock's monthly excess
             return on Fama-French Mkt-RF, SMB, HML over the prior 36 months;
             score = sum of residuals for months t-12..t-2 divided by their
             standard deviation. Factors from Kenneth French's library
             (data/factors, through 2026-08).
  overnight  Lou, Polk & Skouras (2019): 12-1 momentum built only from
             overnight returns (open / prior close), days t-252..t-21.
All variants keep the trend filter, top 10 and 63-day books.

DATASETS
  LIVE   S&P 500 + MidCap 400 point-in-time, books 2017-10 .. 2026-06 (the
         residual's 36 months of history set the start; every arm uses it)
  CRASH  S&P 500 point-in-time, books 2000-01 .. 2015-12
Random control: 10 random eligible names, 5 seeds, the SAME trend filter and
the same exposure as the arm -- so cash time cannot pass for skill.

PASS, per variant, ALL FOUR (decided now; family of three, so the t bar is
the Bonferroni level for 3 tries, about 2.4):
  1  LIVE  variant minus random per book > 0 with Newey-West t (lag 2) >= 2.4
  2  LIVE  the same with the variant's 5 largest contributing names removed
           from the universe: still > 0 (the five-name warning, WORKLIST §31)
  3  Sharpe of the three-tranche account >= the engine's, on BOTH datasets
  4  CRASH max drawdown <= the engine's + 5 percentage points
Survivorship (delisted names unpriced) biases every arm up; the random
control and the engine share it.
"""

from __future__ import annotations

import asyncio
import io
import math
import random
import statistics
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.sources import index_history
from scanner.sources.index_history import SP400, SP500

sys.path.insert(0, "scripts")
from momentum_guard_test import account, union_membership, window  # noqa: E402
from momentum_hac_test import newey_west_t  # noqa: E402

FF = Path("data/factors/ff3_monthly.zip")
HOLD, TOP, COST, SEEDS = 63, 10, 0.0025, (1, 2, 3, 4, 5)
SMA, VOL_DAYS, VOL_TARGET = 210, 126, 0.15
T_BAR, DD_SLACK = 2.4, 5.0
ARMS = ("engine", "vol", "residual", "overnight")
DATASETS = (("LIVE", (SP500, SP400), date(2014, 10, 1), date(2026, 10, 2), "12y",
             date(2017, 10, 1), date(2026, 6, 30)),
            ("CRASH", (SP500,), date(1996, 10, 1), date(2016, 6, 30), "30y",
             date(2000, 1, 1), date(2015, 12, 31)))


def load_ff() -> dict[tuple[int, int], tuple[float, float, float, float]]:
    text = zipfile.ZipFile(FF).read(zipfile.ZipFile(FF).namelist()[0]).decode("latin1")
    out = {}
    for line in io.StringIO(text):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5 and len(parts[0]) == 6 and parts[0].isdigit():
            ym = int(parts[0])
            out[(ym // 100, ym % 100)] = tuple(float(x) / 100 for x in parts[1:])
        elif out and len(parts[0]) == 4:      # annual block follows the monthly one
            break
    return out


def ols_residuals(y, xs):
    """Residuals of y on [1, x1, x2, x3] by the normal equations."""
    rows = [[1.0, *x] for x in xs]
    k = 4
    a = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * v for r, v in zip(rows, y)) for i in range(k)]
    for c in range(k):                                    # Gaussian elimination
        p = max(range(c, k), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p], b[c], b[p] = a[p], a[c], b[p], b[c]
        for r in range(k):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y_ for x, y_ in zip(a[r], a[c])]
                b[r] -= f * b[c]
    beta = [b[i] / a[i][i] for i in range(k)]
    return [v - sum(bi * xi for bi, xi in zip(beta, r)) for r, v in zip(rows, y)]


class Stock:
    """Everything one symbol needs, computed once."""

    def __init__(self, bars):
        self.bars = bars
        self.ix = {d: i for i, d in enumerate(bars.dates)}
        c, o = bars.closes, bars.opens
        self.cum_on, self.cum_ret = [0.0], [0.0]
        for i in range(1, len(c)):
            self.cum_on.append(self.cum_on[-1] + math.log(o[i] / c[i - 1]) if o[i] > 0 and c[i - 1] > 0 else self.cum_on[-1])
            self.cum_ret.append(self.cum_ret[-1] + math.log(c[i] / c[i - 1]) if c[i] > 0 and c[i - 1] > 0 else self.cum_ret[-1])
        self.month_end = {}                                   # (y, m) -> close
        for d, close in zip(bars.dates, c):
            self.month_end[(d.year, d.month)] = close

    def mom12_1(self, j):
        c = self.bars.closes
        return c[j - 21] / c[j - 252] - 1 if j >= 252 else None

    def overnight(self, j):
        return math.exp(self.cum_on[j - 21] - self.cum_on[j - 252]) - 1 if j >= 252 else None

    def residual(self, when, ff):
        months, y, m = [], when.year, when.month
        for _ in range(37):
            months.append((y, m))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        months.reverse()                                       # 37 month-ends -> 36 returns
        closes = [self.month_end.get(k) for k in months]
        if any(v is None for v in closes) or any(k not in ff for k in months[1:]):
            return None
        rets = [closes[i] / closes[i - 1] - 1 - ff[months[i]][3] for i in range(1, 37)]
        e = ols_residuals(rets, [ff[k][:3] for k in months[1:]])
        if e is None:
            return None
        window_ = e[-12:-1]                                    # t-12 .. t-2
        sd = statistics.stdev(window_)
        return sum(window_) / sd if sd else None


def book_return(stocks, picks, entry, exit_):
    return statistics.fmean(stocks[s].bars.closes[stocks[s].ix[exit_]] /
                            stocks[s].bars.closes[stocks[s].ix[entry]] - 1 for s in picks) - COST


def picks_vol(stocks, picks, when):
    """Annualised vol of an equal-weight portfolio of `picks`, prior 126 days."""
    days = []
    for s in picks:
        st, j = stocks[s], stocks[s].ix[when]
        if j < VOL_DAYS:
            return None
        days.append([st.cum_ret[k] - st.cum_ret[k - 1] for k in range(j - VOL_DAYS + 1, j + 1)])
    port = [statistics.fmean(col) for col in zip(*days)]
    return statistics.stdev(port) * 252 ** 0.5


def run(stocks, bench, members, ends, ff, arm, drop=frozenset()):
    """Per book: (date, arm return, random return, {symbol: contribution})."""
    out = []
    for e in ends:
        when, entry, exit_ = bench.dates[e], bench.dates[e + 1], bench.dates[e + 1 + HOLD]
        closes = bench.closes[:e + 1]
        trend = len(closes) > SMA and closes[-1] > statistics.fmean(closes[-SMA:])
        eligible = [s for s in members(when) if s in stocks and s not in drop
                    and all(d in stocks[s].ix for d in (when, entry, exit_))]
        scores = {}
        for s in eligible:
            j = stocks[s].ix[when]
            st = stocks[s]
            score = (st.residual(when, ff) if arm == "residual" else
                     st.overnight(j) if arm == "overnight" else st.mom12_1(j))
            if score is not None:
                scores[s] = score
        if len(scores) < 50:
            continue
        picks = [s for s, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]]
        exposure = 1.0 if trend else 0.0
        if arm == "vol" and trend:
            vol = picks_vol(stocks, picks, when)
            exposure = min(1.0, VOL_TARGET / vol) if vol else 1.0
        ret = book_return(stocks, picks, entry, exit_) * exposure
        rnd = []
        pool = sorted(scores)
        for seed in SEEDS:
            rng = random.Random(seed * 100_003 + e)
            rnd.append(book_return(stocks, rng.sample(pool, TOP), entry, exit_) * exposure)
        contrib = {s: (stocks[s].bars.closes[stocks[s].ix[exit_]] /
                       stocks[s].bars.closes[stocks[s].ix[entry]] - 1) / TOP * exposure for s in picks}
        out.append((when, ret, statistics.fmean(rnd), contrib))
    return out


def summary(books):
    series = [r * 100 for _, r, _, _ in books]
    gap = [(r - x) * 100 for _, r, x, _ in books]
    total, dd, worst = account(series)
    equity, steps = [1 / 3] * 3, []
    prev = 1.0
    for i, r in enumerate(series):
        equity[i % 3] *= 1 + r / 100
        steps.append(sum(equity) / prev - 1)
        prev = sum(equity)
    sharpe = statistics.fmean(steps) / statistics.stdev(steps) * 12 ** 0.5 if len(steps) > 2 else 0.0
    return {"mean": statistics.fmean(series), "gap": statistics.fmean(gap), "t": newey_west_t(gap, 2),
            "total": total, "dd": dd, "worst": worst, "sharpe": sharpe}


async def load(specs, lo, hi, range_, client):
    histories = [await index_history.load(client, spec=s) for s in specs]
    symbols = tuple(sorted(set().union(*(h.symbols_ever(lo, hi, collapse_share_classes=True)
                                         for h in histories))))
    bench = window(await fetch_bars("^GSPC", client, range_=range_), lo, hi)
    raw, errors = await fetch_many(symbols, client, range_=range_)
    stocks = {s: Stock(w) for s, b in raw.items() if (w := window(b, lo, hi)) is not None and len(w) > 300}
    return stocks, bench, union_membership(histories), len(symbols), len(errors)


async def main() -> int:
    ff = load_ff()
    results = {}
    async with httpx.AsyncClient(timeout=60) as client:
        for name, specs, lo, hi, rng, b_lo, b_hi in DATASETS:
            stocks, bench, members, n_sym, n_err = await load(specs, lo, hi, rng, client)
            ends = [i for i in range(len(bench.dates) - 1)
                    if bench.dates[i + 1].month != bench.dates[i].month
                    and b_lo <= bench.dates[i] <= b_hi and i + 1 + HOLD < len(bench.dates)]
            print(f"\n{name}: {' + '.join(s.label for s in specs)}, {n_sym} symbols ever, "
                  f"{len(stocks)} priced, {n_err} fetch errors, {len(ends)} month-ends")
            print(f"   {'arm':<10}{'mean/book':>10}{'vs rand':>9}{'NW t':>7}{'Sharpe':>8}"
                  f"{'total':>8}{'maxDD':>7}{'worst':>8}   top-5 removed: vs rand, t, names")
            results[name] = {}
            for arm in ARMS:
                books = run(stocks, bench, members, ends, ff, arm)
                s = summary(books)
                line = (f"   {arm:<10}{s['mean']:>+9.2f}%{s['gap']:>+8.2f}pp{s['t']:>7.2f}{s['sharpe']:>8.2f}"
                        f"{s['total']:>+7.0f}%{s['dd']:>6.1f}%{s['worst']:>+7.1f}%")
                if name == "LIVE":
                    contrib = defaultdict(float)
                    for _, _, _, c in books:
                        for sym, v in c.items():
                            contrib[sym] += v
                    top5 = frozenset(sorted(contrib, key=lambda k: -contrib[k])[:5])
                    s2 = summary(run(stocks, bench, members, ends, ff, arm, drop=top5))
                    s["gap_drop"], s["t_drop"] = s2["gap"], s2["t"]
                    line += f"   {s2['gap']:+.2f}pp t {s2['t']:.2f}  ({', '.join(sorted(top5))})"
                print(line)
                results[name][arm] = s
    print(f"\nPASS = LIVE vs-random t >= {T_BAR}  AND  LIVE top-5-removed vs-random > 0  AND  "
          f"Sharpe >= engine's on both  AND  CRASH maxDD <= engine's + {DD_SLACK:.0f}pp")
    eng_l, eng_c = results["LIVE"]["engine"], results["CRASH"]["engine"]
    for arm in ARMS[1:]:
        lv, cr = results["LIVE"][arm], results["CRASH"][arm]
        checks = (lv["gap"] > 0 and lv["t"] >= T_BAR, lv["gap_drop"] > 0,
                  lv["sharpe"] >= eng_l["sharpe"] and cr["sharpe"] >= eng_c["sharpe"],
                  cr["dd"] <= eng_c["dd"] + DD_SLACK)
        marks = "  ".join(f"{k} {'ok' if c else 'X'}" for k, c in zip(("t", "top5", "sharpe", "dd"), checks))
        print(f"   {arm:<10}{marks}   ->  {'PASS' if all(checks) else 'fail'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
