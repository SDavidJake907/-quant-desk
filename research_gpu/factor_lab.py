"""GPU factor lab: a factor library, quintile tests, a fixed blend, and one
multiple-testing correction over all of it. Runs on the RTX 5060 Ti (Blackwell).

    <GPU python> research_gpu/factor_lab.py            # both panels
    <GPU python> research_gpu/factor_lab.py --force    # allow during market hours

GPU python = C:/Users/kitti/AppData/Local/Microsoft/WindowsApps/python.exe
(torch 2.11 cu128, sm_120). It imports nothing from `scanner`: it reads
data/factors/panel_<NAME>.pkl written by scripts/factor_export.py in the
project venv. READ-ONLY research. Refuses market hours (Gemma owns the GPU).

FIXED 2026-10-06, COMMITTED BEFORE THIS FILE FIRST RAN. TRIALS.md #46-#55,
ONE family under one correction -- which is the point of this file.

FACTORS (month-end, data through that close; direction stated in advance)
  mom12_1    return t-252 -> t-21                                   +
  overnight  12-1 built from overnight returns only (Lou, Polk, Skouras) +
  mom12_7    return t-252 -> t-126 (Novy-Marx)                      +
  hi52       close / 252-day high (George & Hwang)                  +
  season     mean same-calendar-month return, past 5 years (Heston & Sadka) +
  rev1m      return over the last 21 days                           -
  vol63      63-day daily-return volatility (low-volatility effect) -
  max21      largest daily return in 21 days (Bali et al.)          -
BLENDS (no tuning)
  blend_eq   equal-weight average of the 8 direction-adjusted percentile ranks
  blend_wf   walk-forward ridge on those ranks: weights fit on the prior 60
             months only (lambda = 1, never tuned), predicting the next month
TEST, per factor, per month-end: eligible = index members that day with
prices; forward = next 21 trading days from the NEXT close, minus the
eligible average; spread = top quintile minus bottom quintile in the stated
direction. Non-overlapping months, so plain t.
MULTIPLE TESTING: Romano-Wolf stepdown over all 10 (stationary bootstrap of
the monthly spreads, mean block 6 months, 100,000 resamples on the GPU), plus
White's Reality Check p for the best of the 10.
PASS for a factor or blend: Romano-Wolf adjusted p < 0.05 on LIVE (S&P 500 +
400, 2017-2026) AND a positive mean spread in the stated direction on CRASH
(S&P 500, 2000-2015). Survivorship lifts all names; spreads are less exposed
than levels.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
PANELS = ROOT / "data" / "factors"
HOLD, B, BLOCK, SEED = 21, 100_000, 6.0, 7
FACTORS = {"mom12_1": 1, "overnight": 1, "mom12_7": 1, "hi52": 1, "season": 1,
           "rev1m": -1, "vol63": -1, "max21": -1}
NAMES = (*FACTORS, "blend_eq", "blend_wf")
DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")
F64 = torch.float64


def market_hours(now: datetime) -> bool:
    et = now.astimezone(timezone(timedelta(hours=-4)))      # ET during DST; guard only
    return et.weekday() < 5 and (9, 20) <= (et.hour, et.minute) <= (16, 15)


def load(name: str) -> dict:
    p = pickle.loads((PANELS / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    for k in ("open", "high", "low", "close", "volume"):
        p[k] = torch.tensor(p[k], dtype=F64, device=DEV).reshape(t, n)
    p["members"] = {int(k): v for k, v in p["members"].items()}
    return p


def factor_rows(p: dict, ends: list[int]) -> dict[str, torch.Tensor]:
    """Each factor at each month-end row: (len(ends), N)."""
    c, o, h = p["close"], p["open"], p["high"]
    e = torch.tensor(ends, device=DEV)
    ret = c[1:] / c[:-1] - 1
    ret = torch.cat([torch.full_like(c[:1], float("nan")), ret])
    lon = torch.log(o[1:] / c[:-1])
    lon = torch.cat([torch.zeros_like(c[:1]), torch.nan_to_num(lon, nan=0.0)]).cumsum(0)
    r0 = torch.nan_to_num(ret, nan=0.0)
    ok = (~torch.isnan(ret)).to(F64)
    s1, s2, cnt = r0.cumsum(0), (r0 * r0).cumsum(0), ok.cumsum(0)
    w = 63
    m1 = (s1[e] - s1[e - w]) / (cnt[e] - cnt[e - w])
    m2 = (s2[e] - s2[e - w]) / (cnt[e] - cnt[e - w])
    out = {
        "mom12_1": c[e - 21] / c[e - 252] - 1,
        "overnight": torch.exp(lon[e - 21] - lon[e - 252]) - 1,
        "mom12_7": c[e - 126] / c[e - 252] - 1,
        "rev1m": c[e] / c[e - 21] - 1,
        "vol63": torch.sqrt(torch.clamp(m2 - m1 * m1, min=0)),
        "hi52": c[e] / torch.stack([torch.nan_to_num(h[i - 251:i + 1], nan=-1.0).amax(0) for i in ends]),
        "max21": torch.stack([torch.nan_to_num(ret[i - 20:i + 1], nan=-1.0).amax(0) for i in ends]),
    }
    # same-calendar-month seasonality from month-end closes
    mret = c[e[1:]] / c[e[:-1]] - 1                                   # month k+1 return, k = 0..
    season = torch.full((len(ends), c.shape[1]), float("nan"), dtype=F64, device=DEV)
    for k in range(len(ends)):
        past = [k + 1 - 12 * y for y in range(1, 6) if 0 <= k + 1 - 12 * y - 1 < mret.shape[0]]
        if len(past) >= 2:
            season[k] = torch.nanmean(torch.stack([mret[j - 1] for j in past]), 0)
    out["season"] = season
    return out


def pct_rank(x: torch.Tensor) -> torch.Tensor:
    """Percentile rank 0..1 among finite values; NaN stays NaN."""
    r = torch.full_like(x, float("nan"))
    m = ~torch.isnan(x)
    if m.sum() > 1:
        order = x[m].argsort().argsort().to(F64)
        r[m] = order / (m.sum() - 1)
    return r


def spreads(p: dict) -> tuple[list[str], torch.Tensor, list[int]]:
    """Monthly direction-adjusted Q5-Q1 spread for every factor and blend."""
    c, t = p["close"], p["close"].shape[0]
    ends = [i for i in sorted(p["members"]) if i >= 252 and i + 1 + HOLD < t]
    f = factor_rows(p, ends)
    fwd_all = c[torch.tensor(ends, device=DEV) + 1 + HOLD] / c[torch.tensor(ends, device=DEV) + 1] - 1
    rows, ranks_hist, fwd_hist, kept = [], [], [], []
    for k, i in enumerate(ends):
        cols = torch.tensor(p["members"][i], device=DEV, dtype=torch.long)
        fwd = fwd_all[k, cols]
        good = ~torch.isnan(fwd) & ~torch.isnan(c[i, cols])
        if good.sum() < 100:
            continue
        cols, fwd = cols[good], fwd[good]
        fwd = fwd - fwd.mean()
        ranks = torch.stack([pct_rank(f[n][k, cols] * d) for n, d in FACTORS.items()])   # (8, n)
        row = []
        for j in range(len(FACTORS)):
            row.append(quintile_spread(ranks[j], fwd))
        avail = (~torch.isnan(ranks)).sum(0)
        eq = torch.where(avail >= 6, torch.nanmean(ranks, 0), torch.full_like(fwd, float("nan")))
        row.append(quintile_spread(eq, fwd))
        row.append(ridge_spread(ranks_hist, fwd_hist, ranks, fwd))
        ranks_hist.append(ranks)
        fwd_hist.append(fwd)
        rows.append(row)
        kept.append(i)
    return list(NAMES), torch.tensor(rows, dtype=F64, device=DEV).T, kept     # (10, M)


def quintile_spread(score: torch.Tensor, fwd: torch.Tensor) -> float:
    m = ~torch.isnan(score)
    if m.sum() < 50:
        return float("nan")
    s, y = score[m], fwd[m]
    q = s.argsort()
    n5 = len(q) // 5
    return float(y[q[-n5:]].mean() - y[q[:n5]].mean())


def ridge_spread(hist_x, hist_y, x_now, fwd_now) -> float:
    """Weights from the prior 60 months, skipping the most recent (its forward
    window overlaps this month-end), lambda = 1; NaN ranks -> 0.5 (neutral)."""
    if len(hist_x) < 62:
        return float("nan")
    xs = torch.cat([torch.nan_to_num(x, nan=0.5).T - 0.5 for x in hist_x[-61:-1]])
    ys = torch.cat(hist_y[-61:-1])
    k = xs.shape[1]
    beta = torch.linalg.solve(xs.T @ xs + torch.eye(k, dtype=F64, device=DEV), xs.T @ ys)
    pred = (torch.nan_to_num(x_now, nan=0.5).T - 0.5) @ beta
    return quintile_spread(pred, fwd_now)


def stationary_indices(m: int, b: int, gen: torch.Generator) -> torch.Tensor:
    """Politis-Romano stationary bootstrap index matrix (b, m)."""
    start = torch.randint(0, m, (b, 1), generator=gen, device=DEV)
    new = torch.rand((b, m), generator=gen, device=DEV) < 1 / BLOCK
    new[:, 0] = True
    jump = torch.randint(0, m, (b, m), generator=gen, device=DEV)
    idx = torch.empty((b, m), dtype=torch.long, device=DEV)
    idx[:, 0] = start[:, 0]
    for t in range(1, m):
        idx[:, t] = torch.where(new[:, t], jump[:, t], (idx[:, t - 1] + 1) % m)
    return idx


def romano_wolf(x: torch.Tensor) -> tuple[list[float], float]:
    """x: (K, M) spreads, NaN-free columns per row assumed (NaNs -> row mean).
    Returns (stepdown-adjusted p per row, White Reality Check p for the max)."""
    k, m = x.shape
    x = torch.where(torch.isnan(x), torch.nanmean(x, 1, keepdim=True), x)
    mean = x.mean(1)
    sd = x.std(1)
    stat = mean / sd * math.sqrt(m)
    gen = torch.Generator(device=DEV).manual_seed(SEED)
    boot = []
    for chunk in range(0, B, 10_000):
        idx = stationary_indices(m, min(10_000, B - chunk), gen)
        xb = x[:, idx]                                           # (K, b, M)
        boot.append(((xb.mean(2) - mean[:, None]) / sd[:, None] * math.sqrt(m)).T)   # (b, K)
    boot = torch.cat(boot)                                       # (B, K)
    order = stat.argsort(descending=True)
    adj, running, active = [1.0] * k, 0.0, list(order.tolist())
    for j in order.tolist():
        mx = boot[:, active].amax(1)
        pval = float((mx >= stat[j]).to(F64).mean())
        running = max(running, pval)
        adj[j] = running
        active.remove(j)
    rc = float((boot.amax(1) >= stat.max()).to(F64).mean())
    return adj, rc


def run(name: str) -> dict:
    t0 = time.time()
    p = load(name)
    names, x, kept = spreads(p)
    adj, rc = romano_wolf(x)
    res = {}
    print(f"\n{name}: {len(p['symbols'])} symbols, {len(kept)} months "
          f"{p['dates'][kept[0]]}..{p['dates'][kept[-1]]}, {time.time() - t0:.1f}s on {DEV}")
    print(f"   {'factor':<10}{'dir':>4}{'spread/mo':>11}{'t':>7}{'RW adj p':>10}")
    for j, n in enumerate(names):
        v = x[j][~torch.isnan(x[j])]
        mean, t = float(v.mean()), float(v.mean() / v.std() * math.sqrt(len(v)))
        d = {1: "+", -1: "-"}.get(FACTORS.get(n, 0), "=")
        print(f"   {n:<10}{d:>4}{mean * 100:>+10.2f}%{t:>7.2f}{adj[j]:>10.3f}")
        res[n] = {"mean": mean, "t": t, "rw_p": adj[j], "months": len(v)}
    print(f"   White Reality Check p (best of {len(names)}): {rc:.3f}")
    (PANELS / f"spreads_{name}.json").write_text(json.dumps(
        {"names": names, "months": [p["dates"][i] for i in kept],
         "spreads": x.T.tolist()}), encoding="utf-8")
    return {"factors": res, "rc": rc}


def main(force: bool) -> int:
    if market_hours(datetime.now(timezone.utc)) and not force:
        print("market hours: Gemma owns the GPU. Run after the close, or --force.")
        return 1
    print(f"device: {torch.cuda.get_device_name(0) if DEV.type == 'cuda' else 'CPU'}")
    live, crash = run("LIVE"), run("CRASH")
    print("\nPASS = Romano-Wolf adjusted p < 0.05 on LIVE AND positive spread on CRASH")
    for n in NAMES:
        lv, cr = live["factors"][n], crash["factors"][n]
        ok = lv["rw_p"] < 0.05 and lv["mean"] > 0 and cr["mean"] > 0
        print(f"   {n:<10} LIVE p {lv['rw_p']:.3f}  CRASH {cr['mean'] * 100:+.2f}%/mo  ->  "
              f"{'PASS' if ok else 'fail'}")
    return 0


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--force", action="store_true")
    sys.exit(main(a.parse_args().force))
