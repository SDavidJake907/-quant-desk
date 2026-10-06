"""GPU event study of the ten Lo-Mamaysky-Wang chart patterns, and their cards.

    <GPU python> research_gpu/pattern_lab.py           # both panels
    <GPU python> research_gpu/pattern_lab.py --force   # allow during market hours

READ-ONLY research. Refuses market hours. GPU python = the Microsoft Store
Python 3.12 (torch 2.11 cu128, sm_120); reads data/factors/panel_<NAME>.pkl.

THE DEFINITION is src/scanner/patterns/detect.py (stdlib only, imported here).
The GPU smooths every 38-day window and finds the turning points; detect's own
`classify` names the pattern. A PARITY CHECK on 2,000 random windows against
detect.detect() must match 100% or the run stops.

FIXED 2026-10-06, COMMITTED BEFORE THIS FILE FIRST RAN. TRIALS.md #56-#65, ONE
family (the ten patterns):
  event      a stock is an index member (latest month-end list) and a pattern
             code appears on day e that it did not have on day e-1
  outcome    return from the close of e+1 to e+1+H, minus the average of all
             members' returns over the same days; H = 20 primary, 5 and 60 shown
  series     per pattern, the mean 20-day excess of events grouped by month
  test       Romano-Wolf stepdown over the ten, TWO-SIDED (|t|), stationary
             bootstrap of the monthly series, mean block 2, 100,000 resamples
  MEASURED   adjusted p < 0.05 on LIVE (S&P 500 + 400, 2015-2026) AND the same
             sign on CRASH (S&P 500, 1997-2016); anything else is
             "no measured edge" -- and that is what Gemma is told
Output data/factors/pattern_stats.json feeds the live pattern cards.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from scanner.patterns import detect as D  # noqa: E402  (stdlib-only module)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factor_lab import DEV, F64, market_hours, stationary_indices  # noqa: E402

HORIZONS, PRIMARY, B, BLOCK, SEED = (5, 20, 60), 20, 100_000, 2.0, 7
STATS = ROOT / "data" / "factors" / "pattern_stats.json"
KERNEL = torch.tensor(D._WEIGHTS, dtype=F64, device=DEV)            # (38, 38), row-normalised


def load(name):
    p = pickle.loads((ROOT / "data" / "factors" / f"panel_{name}.pkl").read_bytes())
    t, n = len(p["dates"]), len(p["symbols"])
    p["close"] = torch.tensor(p["close"], dtype=F64, device=DEV).reshape(t, n)
    p["members"] = {int(k): set(v) for k, v in p["members"].items()}
    return p


def windows_for(col: torch.Tensor):
    """All 38-day windows of one stock with no missing close: (ends, X)."""
    x = col.unfold(0, D.WINDOW, 1)                                   # (T-37, 38)
    ok = ~torch.isnan(x).any(1)
    ends = torch.nonzero(ok).squeeze(1) + D.WINDOW - 1
    return ends, x[ok]


def ext_lists(x: torch.Tensor) -> list[list[tuple[int, float, str]]]:
    """Turning points per window, matching detect.extrema exactly."""
    s = x @ KERNEL.T
    before = s[:, 1:D.PATTERN_DAYS - 1] - s[:, 0:D.PATTERN_DAYS - 2]
    after = s[:, 2:D.PATTERN_DAYS] - s[:, 1:D.PATTERN_DAYS - 1]
    is_max = (before > 0) & (after <= 0)
    is_min = (before < 0) & (after >= 0)
    near = x[:, 0:D.PATTERN_DAYS].unfold(1, 3, 1)                    # k-1..k+1 for k = 1..33
    hi, lo = near.amax(2), near.amin(2)
    is_max, is_min, hi, lo = is_max.cpu().tolist(), is_min.cpu().tolist(), hi.cpu().tolist(), lo.cpu().tolist()
    out = []
    for r in range(len(is_max)):
        row = []
        for k in range(D.PATTERN_DAYS - 2):
            if is_max[r][k]:
                row.append((k + 1, hi[r][k], "max"))
            elif is_min[r][k]:
                row.append((k + 1, lo[r][k], "min"))
        out.append(row)
    return out


def parity(p, samples=2000) -> None:
    rng = random.Random(SEED)
    c = p["close"]
    t, n = c.shape
    checked = 0
    while checked < samples:
        j, e = rng.randrange(n), rng.randrange(D.WINDOW - 1, t)
        w = c[e - D.WINDOW + 1:e + 1, j]
        if torch.isnan(w).any():
            continue
        ext = ext_lists(w.unsqueeze(0))[0]
        gpu = D.classify(ext)
        cpu = D.detect(w.cpu().tolist())[0]
        if gpu != cpu:
            raise SystemExit(f"PARITY FAILED at stock {j} day {e}: gpu {gpu} cpu {cpu}")
        checked += 1
    print(f"   parity: {samples}/{samples} random windows identical to detect.detect()")


def events(p):
    c = p["close"]
    t, n = c.shape
    month_ends = sorted(p["members"])
    member_at = {}
    k = 0
    for e in range(t):
        while k + 1 < len(month_ends) and month_ends[k + 1] <= e:
            k += 1
        member_at[e] = p["members"][month_ends[k]] if month_ends and month_ends[0] <= e else set()
    fwd = {h: torch.full_like(c, float("nan")) for h in HORIZONS}
    for h in HORIZONS:
        fwd[h][: t - 1 - h] = c[1 + h:] / c[1: t - h] - 1             # close e+1 -> e+1+h
    mean = {h: torch.nanmean(fwd[h], 1) for h in HORIZONS}
    fwd = {h: v.cpu() for h, v in fwd.items()}
    mean = {h: v.cpu() for h, v in mean.items()}
    found = []
    for j in range(n):
        ends, x = windows_for(c[:, j])
        if len(ends) == 0:
            continue
        codes = [D.classify(ext) for ext in ext_lists(x)]
        prev, prev_e = None, -2
        for e, code in zip(ends.tolist(), codes):
            fresh = code is not None and not (code == prev and e == prev_e + 1)
            prev, prev_e = code, e
            if not fresh or j not in member_at[e] or e + 1 + max(HORIZONS) >= t:
                continue
            ex = {h: float(fwd[h][e, j] - mean[h][e]) for h in HORIZONS}
            if any(math.isnan(v) for v in ex.values()):
                continue
            found.append((code, e, ex))
    return found


def monthly(found, dates):
    by = defaultdict(lambda: defaultdict(list))
    for code, e, ex in found:
        by[code][dates[e][:7]].append(ex[PRIMARY])
    return by


def romano_wolf_two_sided(series: dict[str, list[float]]) -> dict[str, float]:
    names = [k for k in D.CODES if len(series.get(k, [])) >= 12]
    m = max(len(series[k]) for k in names)
    x = torch.full((len(names), m), float("nan"), dtype=F64, device=DEV)
    for i, k in enumerate(names):
        x[i, :len(series[k])] = torch.tensor(series[k], dtype=F64, device=DEV)
    lens = (~torch.isnan(x)).sum(1)
    x = torch.where(torch.isnan(x), torch.nanmean(x, 1, keepdim=True), x)
    mean, sd = x.mean(1), x.std(1)
    stat = (mean / sd * torch.sqrt(lens.to(F64))).abs()
    gen = torch.Generator(device=DEV).manual_seed(SEED)
    boot = []
    for chunk in range(0, B, 10_000):
        idx = stationary_indices(m, min(10_000, B - chunk), gen)
        xb = x[:, idx]
        boot.append(((xb.mean(2) - mean[:, None]) / sd[:, None] * torch.sqrt(lens.to(F64))[:, None]).abs().T)
    boot = torch.cat(boot)
    order = stat.argsort(descending=True).tolist()
    adj, running, active = {}, 0.0, list(order)
    for i in order:
        running = max(running, float((boot[:, active].amax(1) >= stat[i]).to(F64).mean()))
        adj[names[i]] = running
        active.remove(i)
    return adj


def summarise(name):
    t0 = time.time()
    p = load(name)
    print(f"\n{name}: {len(p['symbols'])} symbols x {len(p['dates'])} days")
    parity(p)
    found = events(p)
    by = monthly(found, p["dates"])
    series = {k: [sum(v) / len(v) for _, v in sorted(by[k].items())] for k in by}
    adj = romano_wolf_two_sided(series)
    rows = {}
    print(f"   {len(found)} pattern events, {time.time() - t0:.0f}s")
    print(f"   {'code':<6}{'n':>7}{'EV5':>8}{'EV20':>8}{'EV60':>8}{'hit20':>7}{'t20':>7}{'RW p':>7}")
    for k in D.CODES:
        ev = [x for c, _, x in found if c == k]
        if not ev:
            continue
        s = series.get(k, [])
        t = (sum(s) / len(s)) / (torch.tensor(s).std().item() / math.sqrt(len(s))) if len(s) > 2 else 0.0
        row = {"n": len(ev), "months": len(s), "t": t, "rw_p": adj.get(k, 1.0),
               "hit20": sum(x[20] > 0 for x in ev) / len(ev)}
        for h in HORIZONS:
            row[f"ev{h}"] = sum(x[h] for x in ev) / len(ev) * 100
        rows[k] = row
        print(f"   {k:<6}{row['n']:>7}{row['ev5']:>+7.2f}%{row['ev20']:>+7.2f}%{row['ev60']:>+7.2f}%"
              f"{row['hit20'] * 100:>6.0f}%{t:>7.2f}{row['rw_p']:>7.3f}")
    return rows


def main(force: bool) -> int:
    if market_hours(datetime.now(timezone.utc)) and not force:
        print("market hours: Gemma owns the GPU. Run after the close, or --force.")
        return 1
    live, crash = summarise("LIVE"), summarise("CRASH")
    out = {"made": datetime.now(timezone.utc).isoformat(), "definition": "LMW 2000, detect.py",
           "primary_horizon": PRIMARY, "patterns": {}}
    print("\nMEASURED = Romano-Wolf adj p < 0.05 on LIVE (two-sided) AND same sign on CRASH")
    for k in D.CODES:
        lv, cr = live.get(k), crash.get(k)
        if not lv:
            continue
        same = cr is not None and (cr["ev20"] > 0) == (lv["ev20"] > 0)
        verdict = "MEASURED" if lv["rw_p"] < 0.05 and same else "no measured edge"
        out["patterns"][k] = {**{f: lv[f] for f in ("n", "ev5", "ev20", "ev60", "hit20", "t", "rw_p")},
                              "crash_ev20": cr["ev20"] if cr else None, "verdict": verdict}
        print(f"   {k:<6} LIVE EV20 {lv['ev20']:+.2f}% p {lv['rw_p']:.3f} | CRASH "
              f"{(cr or {}).get('ev20', float('nan')):+.2f}%  ->  {verdict}")
    STATS.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nstats -> {STATS}")
    return 0


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--force", action="store_true")
    sys.exit(main(a.parse_args().force))
