"""Per-stock sizing: does speed, size or beta change what a trade earns, and how
should the position be sized for it? Numbers for docs/SIZING.md.

    python scripts/sizing_research.py

READ-ONLY research. Changes nothing live. Uses every CPU core.

PART 1 -- WHICH STOCKS EARN MORE R?
For every Mon-Thu setup in the 2y Alpaca store (Friday flatten, WORKLIST §29),
point-in-time at the trigger:
    stop %        distance entry -> stop
    ATR30 %       14-bar Wilder ATR on 30m bars, % of price
    daily vol %   20-day stdev of daily returns, annualised
    ATRd %        14-day mean true range, % of price
    $vol M/bar    20-bar mean dollar volume per 30m bar (the size/liquidity proxy)
    beta          252-day beta to the S&P 500
Terciles are cut on TRAIN only and applied unchanged to the holdout. A feature
"sorts" only if the same tercile is best in both halves.

PART 2 -- THE ACCOUNT UNDER FIVE SIZING SCHEMES (5 slots, compounding,
cash-limited, 10bps round trip, Friday flatten)
    A  current      1% risk, 20% position cap (the cap binds on nearly every trade)
    B  equal risk   every trade risks 0.45% (current average), no position cap
                    beyond available cash
    C  vol target   position = 20% x (median daily vol / this stock's), 5-30%
    D  liquidity    cap 25% / 20% / 15% by dollar-volume tercile (big -> small)
    E  beta         cap 20% / beta, clipped to 10-30%
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STORE = "data/intraday_alpaca"
FEATURES = ("stop %", "ATR30 %", "daily vol %", "ATRd %", "$vol M/bar", "beta")
START, COST = 100_000.0, 0.0010


def _daily_feats(dates, closes, highs, lows, bdates, bcloses, when):
    cut = sum(1 for d in dates if d < when)
    bcut = sum(1 for d in bdates if d < when)
    out = {"daily vol %": None, "ATRd %": None, "beta": None}
    if cut > 21:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(cut - 20, cut)]
        out["daily vol %"] = statistics.stdev(rets) * 252 ** 0.5 * 100
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
               for i in range(cut - 14, cut)]
        out["ATRd %"] = statistics.fmean(trs) / closes[cut - 1] * 100
    if cut > 253 and bcut > 253:
        bmap = {bdates[i]: bcloses[i] for i in range(bcut)}
        pairs = [(closes[i] / closes[i - 1] - 1, bmap[dates[i]] / bmap[dates[i - 1]] - 1)
                 for i in range(cut - 252, cut) if dates[i] in bmap and dates[i - 1] in bmap]
        if len(pairs) > 200:
            sx = [p[1] for p in pairs]
            sy = [p[0] for p in pairs]
            mx, my = statistics.fmean(sx), statistics.fmean(sy)
            var = sum((x - mx) ** 2 for x in sx)
            out["beta"] = sum((x - mx) * (y - my) for x, y in zip(sx, sy)) / var if var else None
    return out


def _work(args):
    """One symbol: every setup with its features and Friday-flatten outcome."""
    symbol, daily, bench = args
    from scanner.execution.policy import TradingPolicy
    from scanner.execution.session import crosses_session
    from scanner.features import history
    from scanner.features.intraday import SESSION_ALIGNED
    from scanner.screens.smc import scan
    sys.path.insert(0, "scripts")
    from mfe_trail_test import wilder_atr

    V = TradingPolicy()
    history.STORE_DIR = Path(STORE)
    bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
    if bars is None or len(bars) < 60:
        return []
    out = []
    for s in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                  min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
        i = s.trigger_index
        if crosses_session(bars, i) or s.entry_price - s.stop_price <= 0:
            continue
        if bars.times[i].weekday() >= V.wind_down_from_weekday or i + V.max_hold_bars > len(bars) - 1:
            continue
        lo = max(0, i - 20)
        dvol = statistics.fmean(bars.volumes[k] * bars.closes[k] for k in range(lo, i)) if i > lo else 0
        if bars.closes[i] < V.min_price or dvol < V.min_dollar_volume:
            continue                                       # live liquidity gates
        entry, stop, target = s.entry_price, s.stop_price, s.target_price
        risk = entry - stop
        last = min(i + V.max_hold_bars, len(bars) - 1)
        exit_price, exit_i = bars.closes[last], last
        for j in range(i + 1, last + 1):
            o, h, l = bars.opens[j], bars.highs[j], bars.lows[j]
            if o <= stop or o >= target:
                exit_price, exit_i = o, j
                break
            if l <= stop:
                exit_price, exit_i = stop, j
                break
            if h >= target:
                exit_price, exit_i = target, j
                break
            if bars.times[j].weekday() == 4 and (j == last or bars.times[j + 1].date() != bars.times[j].date()):
                exit_price, exit_i = bars.closes[j], j
                break
        atr = wilder_atr(bars, i)
        feats = {"stop %": risk / entry * 100,
                 "ATR30 %": atr / entry * 100 if atr else None,
                 "$vol M/bar": dvol / 1e6}
        if daily is not None:
            feats.update(_daily_feats(*daily, *bench, bars.times[i].date()))
        else:
            feats.update({"daily vol %": None, "ATRd %": None, "beta": None})
        out.append({"at": bars.times[i], "symbol": symbol, "entry": entry, "stop": stop,
                    "exit": exit_price, "exit_at": bars.times[exit_i],
                    "r": (exit_price - entry) / risk, "f": feats})
    return out


def terciles(rows, key):
    vals = sorted(r["f"][key] for r in rows if r["f"][key] is not None)
    return vals[len(vals) // 3], vals[2 * len(vals) // 3]


def band(value, cuts):
    if value is None:
        return None
    return "low" if value < cuts[0] else "high" if value >= cuts[1] else "mid"


def part1(rows, cut):
    train, hold = rows[:cut], rows[cut:]
    print(f"\nPART 1 -- R BY TERCILE  ({len(rows)} gated setups; train {len(train)} to "
          f"{train[-1]['at']:%Y-%m-%d}, holdout {len(hold)}; cuts from train)")
    print(f"  {'feature':<13}{'cuts (low|high)':<20}{'':4}{'low':>16}{'mid':>16}{'high':>16}   verdict")
    found = {}
    for key in FEATURES:
        cuts = terciles(train, key)
        cells, best = {}, {}
        for name, part in (("train", train), ("hold", hold)):
            means = {}
            for b in ("low", "mid", "high"):
                v = [r["r"] for r in part if band(r["f"][key], cuts) == b]
                means[b] = (statistics.fmean(v), len(v)) if v else (0.0, 0)
            cells[name] = means
            best[name] = max(means, key=lambda b: means[b][0])
        stable = best["train"] == best["hold"]
        found[key] = (cuts, best["train"] if stable else None)
        for name in ("train", "hold"):
            row = "".join(f"{cells[name][b][0]:>+10.3f} ({cells[name][b][1]:>3})" for b in ("low", "mid", "high"))
            label = (f"{key:<13}{f'{cuts[0]:.2f} | {cuts[1]:.2f}':<20}" if name == "train" else " " * 33)
            verdict = (f"best={best['train']} in BOTH" if stable else "flips") if name == "train" else ""
            print(f"  {label}{name:<4}{row}   {verdict}")
    return found


def simulate(trades, size_fn):
    from scanner.execution.policy import TradingPolicy
    V = TradingPolicy()
    equity, peak, max_dd = START, START, 0.0
    open_, risks, taken, skipped = [], [], 0, 0
    for t in trades:
        for p in sorted([p for p in open_ if p["exit_at"] <= t["at"]], key=lambda p: p["exit_at"]):
            equity += p["pnl"]
            open_.remove(p)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
        if any(p["symbol"] == t["symbol"] for p in open_) or len(open_) >= V.max_positions:
            continue
        free = equity - sum(p["value"] for p in open_)
        shares = int(min(size_fn(t, equity), free) // t["entry"])
        if shares < 1:
            skipped += 1
            continue
        value = shares * t["entry"]
        risks.append(shares * (t["entry"] - t["stop"]) / equity * 100)
        open_.append({"symbol": t["symbol"], "exit_at": t["exit_at"], "value": value,
                      "pnl": shares * (t["exit"] - t["entry"]) - value * COST})
        taken += 1
    for p in open_:
        equity += p["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    ret = equity / START - 1
    return ret * 100, max_dd * 100, (ret / max_dd if max_dd else 0.0), \
        (statistics.fmean(risks) if risks else 0.0), (statistics.pstdev(risks) if risks else 0.0), taken


def part2(rows, cut, found):
    train = rows[:cut]
    vols = sorted(r["f"]["daily vol %"] for r in train if r["f"]["daily vol %"])
    med_vol = vols[len(vols) // 2]
    dcuts = found["$vol M/bar"][0]

    def current(t, eq):
        return min(eq * 0.01 / (t["entry"] - t["stop"]) * t["entry"], eq * 0.20)

    def equal_risk(t, eq):
        return eq * 0.0045 / (t["entry"] - t["stop"]) * t["entry"]

    def vol_target(t, eq):
        v = t["f"]["daily vol %"] or med_vol
        return eq * min(0.30, max(0.05, 0.20 * med_vol / v))

    def liquidity(t, eq):
        b = band(t["f"]["$vol M/bar"], dcuts)
        return eq * {"high": 0.25, "mid": 0.20, "low": 0.15}.get(b, 0.20)

    def beta(t, eq):
        bt = t["f"]["beta"] or 1.0
        return eq * min(0.30, max(0.10, 0.20 / max(bt, 0.1)))

    schemes = (("A current (1%, 20% cap)", current), ("B equal risk 0.45%", equal_risk),
               ("C vol target", vol_target), ("D liquidity tiers", liquidity), ("E beta", beta))
    print(f"\nPART 2 -- THE ACCOUNT  (5 slots, compounding, cash-limited, 10bps, Friday flatten; "
          f"median daily vol {med_vol:.1f}%, $vol tercile cuts {dcuts[0]:.1f}/{dcuts[1]:.1f}M)")
    half = len(rows) // 2
    for label, part in (("ALL", rows), ("FIRST HALF", rows[:half]), ("SECOND HALF", rows[half:])):
        print(f"\n  {label}")
        print(f"    {'scheme':<26}{'return':>9}{'max DD':>9}{'ret/DD':>8}{'risk/trade':>12}"
              f"{'risk spread':>13}{'taken':>7}")
        for name, fn in schemes:
            ret, dd, ratio, risk, spread, taken = simulate(part, fn)
            print(f"    {name:<26}{ret:>+8.1f}%{dd:>8.1f}%{ratio:>8.2f}{risk:>11.2f}%{spread:>12.2f}%{taken:>7}")


async def main() -> int:
    from scanner.features.bars import fetch_bars, fetch_many
    symbols = sorted({f.name.split("__")[0] for f in Path(STORE).glob("*.json")})
    async with httpx.AsyncClient(timeout=60) as client:
        bench = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    b = (list(bench.dates), list(bench.closes))
    jobs = [(s, (list(daily[s].dates), list(daily[s].closes), list(daily[s].highs),
                 list(daily[s].lows)) if s in daily else None, b) for s in symbols]
    rows = []
    with ProcessPoolExecutor() as pool:
        for chunk in pool.map(_work, jobs, chunksize=8):
            rows.extend(chunk)
    rows.sort(key=lambda r: r["at"])
    cut = len(rows) * 2 // 3
    found = part1(rows, cut)
    part2(rows, cut, found)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
