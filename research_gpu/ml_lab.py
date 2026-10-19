"""ML lab: can a model find "indicator + indicator = price" in past prices?
Both tails: which stocks it expects to beat the market AND which to fall.

    <GPU python> research_gpu/ml_lab.py              # LIVE then CRASH, the real run
    <GPU python> research_gpu/ml_lab.py --synthetic  # plumbing check: planted signal / pure noise

GPU python = C:/Users/kitti/AppData/Local/Microsoft/WindowsApps/python.exe
(numpy, scikit-learn). Imports nothing from `scanner`; reads the point-in-time
panels data/factors/panel_<NAME>.pkl (scripts/factor_export.py). READ-ONLY.

Operator 2026-10-19: "past prices should show someting, like 1+1 =2 or
indicator+indicator=price ... use the - part also ... like -R2".

RULES FIXED BEFORE THE FIRST REAL RUN (TRIALS #70, WORKLIST §67). Not tuned.

SAMPLE   Month-ends; eligible = index members that day with a price and 252
         days of history. Features use data through that close only.
FEATURES 27 price/volume features (FEATURES below), each turned into a
         cross-sectional percentile rank within the month (no market level).
TARGET   Forward excess return: close of the NEXT session to the close 21
         sessions after it, minus the eligible average. Trained on its
         cross-sectional rank, so both tails count equally.
MODEL    sklearn HistGradientBoostingRegressor, fixed: max_iter 300,
         learning_rate 0.05, max_leaf_nodes 31, min_samples_leaf 500,
         l2_regularization 1.0, random_state 0. Walk-forward: refit each
         January on every earlier month whose forward window closed before the
         first prediction (no overlap), at least 36 months of training.
SCORED   Per prediction month: Spearman RankIC (prediction vs forward excess);
         top and bottom decile mean excess; their spread; and in R (forward
         return / (2 x 14-day ATR%), one stop-width = 1R): each decile's mean R,
         share at or below -2R, share at or above +2R. Momentum alone (12-1
         rank) is scored the same way on the same months.
HOLDOUT  LIVE (S&P 500 + 400, point-in-time): predictions 2022-01 onward,
         read once. 2018-2021 predictions are reported as development.
         CRASH (S&P 500, 1997-2016): predictions 2001 onward, second check.
PASS     All of: on the LIVE holdout, mean RankIC > 0 with t >= 3 (the ledger's
         bar for ideas searched on this desk's data); top-minus-bottom decile
         spread > 0 with t >= 2; mean RankIC above momentum-alone's on the same
         months; and on CRASH, mean RankIC > 0. Anything less is FAIL.
         The bottom decile alone (an "avoid" list) is reported, not graded.
CAVEATS  Survivorship: delisted names are missing, so levels read high;
         ranks and spreads are less exposed. Costs: none deducted; a monthly
         rebalance of a decile costs very roughly 0.1-0.3% a month.
"""

from __future__ import annotations

import argparse
import math
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "data" / "factors"
HOLD = 21
MIN_TRAIN_MONTHS = 36
HOLDOUT_FROM = {"LIVE": "2022-01-01", "CRASH": "2001-01-01"}
FIRST_PRED = {"LIVE": "2018-01-01", "CRASH": "2001-01-01"}
FEATURES = ("r5", "r10", "r21", "r63", "r126", "r252", "mom12_1", "mom12_7",
            "vol21", "vol63", "vol_ratio", "atr14", "hi52", "lo52", "sma20", "sma50",
            "sma200", "sma50_200", "rsi14", "max21", "min21", "skew63", "dollar_vol",
            "volume_ratio", "overnight21", "intraday21", "beta252")
MODEL = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
             l2_regularization=1.0, random_state=0)


def load(name: str) -> dict:
    p = pickle.loads((PANELS / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    for k in ("open", "high", "low", "close", "volume"):
        p[k] = np.asarray(p[k], dtype=np.float64).reshape(t, n)
    p["bench"] = np.asarray(p["bench"], dtype=np.float64)
    p["members"] = {int(k): list(v) for k, v in p["members"].items()}
    p["dates"] = [str(d)[:10] for d in p["dates"]]
    return p


def _win(x: np.ndarray, e: int, w: int) -> np.ndarray:
    return x[e - w + 1:e + 1]


def features_at(p: dict, e: int, cols: np.ndarray) -> np.ndarray:
    """(len(cols), len(FEATURES)) raw features at row e (data through that close)."""
    c, o, h, lo, v = (p[k][:, cols] for k in ("close", "open", "high", "low", "volume"))
    b = p["bench"]
    with np.errstate(all="ignore"):
        ret = c[1:] / c[:-1] - 1
        ret = np.vstack([np.full((1, len(cols)), np.nan), ret])
        r = lambda k: c[e] / c[e - k] - 1                                  # noqa: E731
        rw21, rw63 = _win(ret, e, 21), _win(ret, e, 63)
        vol21, vol63 = np.nanstd(rw21, 0), np.nanstd(rw63, 0)
        prev = c[e - 14:e]
        tr = np.maximum(_win(h, e, 14), prev) - np.minimum(_win(lo, e, 14), prev)
        atr14 = np.nanmean(tr / _win(c, e, 14), 0)
        sma = lambda k: np.nanmean(_win(c, e, k), 0)                       # noqa: E731
        gains = np.nanmean(np.clip(_win(ret, e, 14), 0, None), 0)
        losses = np.nanmean(np.clip(-_win(ret, e, 14), 0, None), 0)
        rsi = 100 - 100 / (1 + gains / np.where(losses > 0, losses, np.nan))
        m63 = np.nanmean(rw63, 0)
        skew = np.nanmean((rw63 - m63) ** 3, 0) / vol63 ** 3
        dv = np.log(np.nanmean(_win(c, e, 21) * _win(v, e, 21), 0))
        vratio = np.nanmean(_win(v, e, 5), 0) / np.nanmean(_win(v, e, 63), 0)
        on = np.nansum(np.log(_win(o, e, 21) / c[e - 21:e]), 0)
        intra = np.nansum(np.log(_win(c, e, 21) / _win(o, e, 21)), 0)
        br = b[e - 251:e + 1] / b[e - 252:e] - 1
        sr = _win(ret, e, 252)
        bc = br - br.mean()
        beta = np.nansum((sr - np.nanmean(sr, 0)) * bc[:, None], 0) / (bc ** 2).sum()
        cols_out = [r(5), r(10), r(21), r(63), r(126), r(252),
                    c[e - 21] / c[e - 252] - 1, c[e - 126] / c[e - 252] - 1,
                    vol21, vol63, vol21 / vol63, atr14,
                    c[e] / np.nanmax(_win(h, e, 252), 0), c[e] / np.nanmin(_win(lo, e, 252), 0),
                    c[e] / sma(20), c[e] / sma(50), c[e] / sma(200), sma(50) / sma(200),
                    rsi, np.nanmax(rw21, 0), np.nanmin(rw21, 0), skew, dv, vratio, on, intra, beta]
    x = np.column_stack(cols_out)
    x[~np.isfinite(x)] = np.nan
    return x


def pct_rank(x: np.ndarray) -> np.ndarray:
    """Column-wise percentile rank in [0, 1]; NaN stays NaN."""
    out = np.full_like(x, np.nan)
    for j in range(x.shape[1]):
        col = x[:, j]
        ok = np.isfinite(col)
        if ok.sum() > 1:
            order = col[ok].argsort().argsort()
            out[ok, j] = order / (ok.sum() - 1)
    return out


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 30:
        return float("nan")
    ra, rb = a[ok].argsort().argsort(), b[ok].argsort().argsort()
    return float(np.corrcoef(ra, rb)[0, 1])


def build(p: dict, synthetic: str | None = None, seed: int = 0) -> list[dict]:
    """One record per month-end: eligible columns, ranked features, forward excess, R."""
    t = len(p["dates"])
    rng = np.random.default_rng(seed)
    months = []
    for e in sorted(p["members"]):
        if e < 260 or e + 1 + HOLD >= t:
            continue
        cols = np.array(p["members"][e], dtype=np.int64)
        c = p["close"][:, cols]
        ok = np.isfinite(c[e]) & np.isfinite(c[e - 252]) & np.isfinite(c[e + 1]) \
            & np.isfinite(c[e + 1 + HOLD])
        cols = cols[ok]
        if len(cols) < 100:
            continue
        x_raw = features_at(p, e, cols)
        fwd = p["close"][e + 1 + HOLD, cols] / p["close"][e + 1, cols] - 1
        excess = fwd - np.nanmean(fwd)
        x = pct_rank(x_raw)
        if synthetic == "planted":        # a real, modest signal on r63's rank
            excess = 0.02 * (x[:, FEATURES.index("r63")] - 0.5) + rng.normal(0, 0.08, len(cols))
        elif synthetic == "noise":
            excess = rng.normal(0, 0.08, len(cols))
        atr = x_raw[:, FEATURES.index("atr14")]
        atr = np.where(atr > 0, atr, np.nan)          # a zero range has no R
        months.append({"row": e, "date": p["dates"][e], "x": x, "excess": excess,
                       "y": pct_rank(excess[:, None])[:, 0],
                       "r": fwd / (2 * atr), "mom": x[:, FEATURES.index("mom12_1")]})
    return months


def walk_forward(months: list[dict], first_pred: str) -> list[dict]:
    """Refit each January on earlier months whose forward window has closed."""
    out = []
    years = sorted({m["date"][:4] for m in months if m["date"] >= first_pred})
    for year in years:
        test = [m for m in months if m["date"][:4] == year]
        first_row = test[0]["row"]
        train = [m for m in months if m["row"] + 1 + HOLD < first_row]
        if len(train) < MIN_TRAIN_MONTHS:
            continue
        xs = np.vstack([m["x"] for m in train])
        ys = np.concatenate([m["y"] for m in train])
        keep = np.isfinite(ys)
        model = HistGradientBoostingRegressor(**MODEL).fit(xs[keep], ys[keep])
        for m in test:
            out.append({**m, "pred": model.predict(m["x"])})
    return out


def month_stats(m: dict, score: np.ndarray) -> dict:
    ex, r = m["excess"], m["r"]
    ok = np.isfinite(score) & np.isfinite(ex)
    s, ex, r = score[ok], ex[ok], r[ok]
    k = max(1, len(s) // 10)
    order = s.argsort()
    bot, top = order[:k], order[-k:]
    return {"ic": spearman(s, ex), "top": ex[top].mean() * 100, "bot": ex[bot].mean() * 100,
            "top_r": np.nanmean(r[top]), "bot_r": np.nanmean(r[bot]), "all_r": np.nanmean(r),
            "top_le2": np.mean(r[top] <= -2), "bot_le2": np.mean(r[bot] <= -2),
            "all_le2": np.mean(r <= -2),
            "top_ge2": np.mean(r[top] >= 2), "bot_ge2": np.mean(r[bot] >= 2),
            "all_ge2": np.mean(r >= 2)}


def tstat(xs) -> tuple[float, float]:
    xs = [x for x in xs if math.isfinite(x)]
    if len(xs) < 3:
        return float("nan"), float("nan")
    m = float(np.mean(xs))
    return m, m / (float(np.std(xs, ddof=1)) / math.sqrt(len(xs)))


def report(label: str, preds: list[dict]) -> dict:
    model = [month_stats(m, m["pred"]) for m in preds]
    mom = [month_stats(m, m["mom"]) for m in preds]
    ic, ic_t = tstat([s["ic"] for s in model])
    mic, mic_t = tstat([s["ic"] for s in mom])
    spread, sp_t = tstat([s["top"] - s["bot"] for s in model])
    top, top_t = tstat([s["top"] for s in model])
    bot, bot_t = tstat([s["bot"] for s in model])
    avg = lambda k: float(np.nanmean([s[k] for s in model]))             # noqa: E731
    print(f"\n{label}: {len(preds)} months ({preds[0]['date']} .. {preds[-1]['date']})")
    print(f"  model RankIC      {ic:+.4f}  t {ic_t:+.2f}")
    print(f"  momentum RankIC   {mic:+.4f}  t {mic_t:+.2f}")
    print(f"  top decile        {top:+.2f}%/month vs average  t {top_t:+.2f}")
    print(f"  bottom decile     {bot:+.2f}%/month vs average  t {bot_t:+.2f}   (the 'avoid' list)")
    print(f"  top - bottom      {spread:+.2f}%/month  t {sp_t:+.2f}")
    print(f"  in R (1R = 2 x ATR14): top {avg('top_r'):+.2f}R  bottom {avg('bot_r'):+.2f}R  "
          f"all {avg('all_r'):+.2f}R per 21 sessions")
    print(f"  share <= -2R: top {avg('top_le2') * 100:.1f}%  bottom {avg('bot_le2') * 100:.1f}%  "
          f"all {avg('all_le2') * 100:.1f}%")
    print(f"  share >= +2R: top {avg('top_ge2') * 100:.1f}%  bottom {avg('bot_ge2') * 100:.1f}%  "
          f"all {avg('all_ge2') * 100:.1f}%")
    return {"ic": ic, "ic_t": ic_t, "mom_ic": mic, "spread": spread, "spread_t": sp_t}


def run(name: str, synthetic: str | None = None) -> dict:
    p = load(name)
    months = build(p, synthetic)
    preds = walk_forward(months, FIRST_PRED[name])
    cut = HOLDOUT_FROM[name]
    dev = [m for m in preds if m["date"] < cut]
    hold = [m for m in preds if m["date"] >= cut]
    if dev:
        report(f"{name} development", dev)
    return report(f"{name} HOLDOUT", hold)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--synthetic", action="store_true")
    args = a.parse_args()
    if args.synthetic:
        for kind in ("planted", "noise"):
            print(f"\n===== SYNTHETIC: {kind} (LIVE panel features, made-up target) =====")
            r = run("LIVE", synthetic=kind)
            print(f"  -> RankIC t {r['ic_t']:+.2f} ({'found' if r['ic_t'] >= 3 else 'nothing'})")
        return 0
    live = run("LIVE")
    crash = run("CRASH")
    checks = {
        "LIVE holdout RankIC > 0, t >= 3": live["ic"] > 0 and live["ic_t"] >= 3,
        "LIVE top-bottom spread > 0, t >= 2": live["spread"] > 0 and live["spread_t"] >= 2,
        "LIVE RankIC above momentum alone": live["ic"] > live["mom_ic"],
        "CRASH RankIC > 0": crash["ic"] > 0,
    }
    print("\nPRE-SET VERDICT (TRIALS #70)")
    for k, ok in checks.items():
        print(f"  {'PASS' if ok else 'fail'}  {k}")
    print("  => " + ("PASS" if all(checks.values()) else "FAIL"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
