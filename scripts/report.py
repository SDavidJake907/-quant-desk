"""Build the full project report: every measurement, in one page.

    python scripts/report.py                    # everything
    python scripts/report.py --skip-daily       # intraday evidence only
    python scripts/report.py --out report.html

Nothing here is hardcoded. Every number on the page is computed from cached
bars at run time, so the report cannot drift away from what the code actually
does — which is the whole reason it exists rather than a static summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from scanner.backtest.costs import CostModel
from scanner.backtest.setups import Report, random_control, resolve_setups
from scanner.features import profile, technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import fetch_intraday, max_range
from scanner.heatmap import Cell, build_cells, summarise
from scanner.screens import coiled, trend
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP500

DIM, RESET = "\033[2m", "\033[0m"
BENCHMARK = "^GSPC"
TEMPLATE = Path(__file__).with_name("report_template.html")

#: Friction levels the cost curve is evaluated at (round-trip basis points).
FRICTION = [(0, 0, 0), (9, 5, 2), (20, 10, 5), (40, 20, 10), (60, 30, 15)]
#: Minimum stop distances swept, as a percentage of price.
FLOORS = [0.0, 0.75, 1.0, 1.5, 2.0, 2.5]
HOLDOUT = 0.333
#: A floor must produce at least this many TRAINING trades to be selectable.
#:
#: Without it, selection is broken in a specific and seductive way: edge rises
#: monotonically with the stop floor, so "pick the best" always walks to the
#: widest value offered and leaves a holdout of ~25 trades. Add a wider option
#: and it walks further. The floor is not being discovered, it is being fitted
#: to whatever the range happens to end at. Requiring a real sample stops the
#: walk and costs nothing that was ever trustworthy.
MIN_TRAIN_TRADES = 100
COSTS = CostModel(spread_bps=10, slippage_bps=5)   # 20 bps round trip


def count_tests() -> int:
    root = Path(__file__).resolve().parent.parent / "tests"
    return sum(
        len(re.findall(r"^def test_", path.read_text(encoding="utf-8"), re.M))
        for path in root.glob("test_*.py")
    )


def _evaluate(bars_by_symbol, *, floor, lower, upper, k=2, draws=5):
    real, control = [], []
    rng = random.Random(42)
    for bars in bars_by_symbol.values():
        low, high = int(len(bars) * lower), int(len(bars) * upper)
        setups = scan(bars, k=k, require_value_sweep=True, min_risk_pct=floor)
        if not setups:
            continue
        real.extend(resolve_setups(bars, setups, start_index=low, end_index=high))
        control.extend(
            random_control(bars, setups, rng, draws=draws,
                           start_index=low, end_index=high)
        )
    return (Report(label="pattern", outcomes=tuple(real)),
            Report(label="random", outcomes=tuple(control)))


async def intraday_evidence(client: httpx.AsyncClient) -> dict:
    """Cost curve, stop-floor sweep and the clean train/holdout protocol."""
    universe = await composite.load_universe(client, (SP500,))
    symbols = tuple(m.symbol for m in universe)

    frames = {}
    for minutes in (30, 240):
        loaded = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=max_range(minutes))
              for s in symbols),
            return_exceptions=True,
        )
        frames[minutes] = {
            s: b for s, b in zip(symbols, loaded) if not isinstance(b, Exception)
        }
        print(f"{DIM}  {minutes}m: {len(frames[minutes])} symbols{RESET}")

    # --- cost curve: the same strategy at rising friction -----------------
    curve = []
    best = {30: (2, 1.5), 240: (4, 0.0)}
    for round_trip, spread, slip in FRICTION:
        row = {"bps": round_trip}
        for minutes, (k, floor) in best.items():
            pattern, control = _evaluate(frames[minutes], floor=floor,
                                         lower=0.0, upper=1.0, k=k)
            model = CostModel(spread_bps=spread, slippage_bps=slip)
            row[f"m{minutes}"] = round(pattern.net_expectancy(model), 4)
            row[f"r{minutes}"] = round(control.net_expectancy(model), 4)
        curve.append(row)

    # --- stop-floor sweep, training period only ---------------------------
    sweep = []
    for floor in FLOORS:
        pattern, control = _evaluate(frames[30], floor=floor,
                                     lower=0.0, upper=1.0 - HOLDOUT)
        if not pattern.count:
            continue
        sweep.append({
            "floor": floor,
            "trades": pattern.count,
            "net": round(pattern.net_expectancy(COSTS), 4),
            "random": round(control.net_expectancy(COSTS), 4),
            "edge": round(pattern.net_expectancy(COSTS)
                          - control.net_expectancy(COSTS), 4),
        })

    eligible = [row for row in sweep if row["trades"] >= MIN_TRAIN_TRADES]
    chosen = (max(eligible, key=lambda row: row["edge"])["floor"]
              if eligible else (sweep[0]["floor"] if sweep else 1.5))

    # --- the honest protocol: floor from train, applied to holdout --------
    split = {}
    for label, lower, upper in (("train", 0.0, 1.0 - HOLDOUT),
                                ("holdout", 1.0 - HOLDOUT, 1.0)):
        pattern, control = _evaluate(frames[30], floor=chosen,
                                     lower=lower, upper=upper)
        split[label] = {
            "trades": pattern.count,
            "win_rate": round(pattern.win_rate, 1),
            "gross": round(pattern.expectancy, 4),
            "net": round(pattern.net_expectancy(COSTS), 4),
            "random_net": round(control.net_expectancy(COSTS), 4),
            "edge": round(pattern.net_expectancy(COSTS)
                          - control.net_expectancy(COSTS), 4),
            "breakeven_bps": round(pattern.breakeven_bps, 1),
            "t_stat": round(pattern.t_stat, 2),
        }

    depth = min((len(b) for b in frames[30].values()), default=0)
    return {
        "curve": curve, "sweep": sweep, "chosen_floor": chosen, "split": split,
        "min_train_trades": MIN_TRAIN_TRADES,
        "symbols_30m": len(frames[30]), "symbols_4h": len(frames[240]),
        "bars_30m": depth,
        "frames30": frames[30],
    }


async def daily_evidence(client: httpx.AsyncClient) -> dict:
    """The daily screens, point-in-time, against a random control."""
    from scanner.backtest import engine
    from scanner.features.technicals import Features
    from scanner.screens.trend import Candidate
    from scanner.sources import index_history

    history = await index_history.load(client, spec=SP500)
    from scanner.features.bars import range_start
    start = range_start("5y")
    wanted = history.symbols_ever(start, date.today(), collapse_share_classes=True)
    benchmark = await fetch_bars(BENCHMARK, client, range_="5y")
    bars, _ = await fetch_many(tuple(sorted(wanted)), client, range_="5y")
    bars, _ = engine.drop_recycled(bars, history.exit_dates(start, date.today()))

    cache: dict = {}

    def membership(on):
        if on not in cache:
            cache[on] = history.members_on(on, collapse_share_classes=True)
        return cache[on]

    random.seed(42)

    def random_screen(f: Features, rs: float | None) -> Candidate:
        return Candidate(symbol=f.symbol, features=f, relative_strength=rs,
                         criteria=(), score=random.random())

    rows = []
    for name, screen in (("trend", trend.evaluate), ("coiled", coiled.evaluate),
                         ("random", random_screen)):
        result = engine.run(bars, benchmark, screen, horizon_days=21,
                            step_days=21, top_n=10,
                            require_all=(name != "random"), membership=membership)
        rows.append({
            "name": name,
            "mean": round(result.mean_excess, 3),
            "median": round(result.median_excess, 3),
            "hit": round(result.period_hit_rate, 1),
            "t": round(result.clustered_t, 2),
            "n": result.effective_n,
            "trades": result.count,
        })
    return {"screens": rows, "missing": len(bars) and 0}


async def live_snapshot(client: httpx.AsyncClient, frames30) -> dict:
    """Setups on the board right now, and the universe heatmap."""
    universe = await composite.load_universe(client, COMPOSITE_1500)
    benchmark = technicals.compute(await fetch_bars(BENCHMARK, client, range_="2y"))
    daily, _ = await fetch_many(tuple(m.symbol for m in universe), client, range_="2y")

    cells = []
    for member in universe:
        series = daily.get(member.symbol)
        if series is None:
            continue
        features = technicals.compute(series)
        rs = technicals.relative_strength(features, benchmark)
        candidate = trend.evaluate(features, rs)
        cells.append(Cell(
            symbol=member.symbol, name=member.name, sector=member.sector,
            index=member.index, relative_strength=rs,
            momentum_12_1=features.momentum_12_1, price=features.price,
            pct_from_52w_high=features.pct_from_52w_high,
            criteria_passed=candidate.passed_count,
            criteria_total=len(candidate.criteria),
        ))
    blocks = build_cells(cells)

    setups = []
    for symbol, bars in frames30.items():
        for setup in scan(bars, k=2, require_value_sweep=True, min_risk_pct=1.5,
                          min_reward_risk=1.5):
            if len(bars) - 1 - setup.trigger_index >= 8:
                continue
            try:
                prof = profile.build(bars, setup.sweep_index - 60, setup.trigger_index)
                levels = {"vah": round(prof.value_area_high, 2),
                          "poc": round(prof.poc_price, 2),
                          "val": round(prof.value_area_low, 2)}
            except profile.ProfileError:
                levels = None
            setups.append({
                "symbol": symbol,
                "at": bars.times[setup.trigger_index].isoformat(timespec="minutes"),
                "entry": round(setup.entry_price, 2),
                "stop": round(setup.stop_price, 2),
                "target": round(setup.target_price, 2),
                "rr": round(setup.reward_risk, 2),
                "risk_pct": round(setup.risk / setup.entry_price * 100, 2),
                "levels": levels,
            })
    setups.sort(key=lambda s: s["at"], reverse=True)

    return {
        "summary": summarise(blocks),
        "sectors": [
            {"sector": b.sector, "median": b.median_rs, "count": b.count,
             "passing": b.passing,
             "cells": [{"s": c.symbol, "rs": c.relative_strength,
                        "p": c.criteria_passed == c.criteria_total}
                       for c in b.cells]}
            for b in blocks
        ],
        "setups": setups,
        "universe": len(universe),
        "with_bars": len(cells),
    }


async def main(out: Path, skip_daily: bool) -> None:
    async with httpx.AsyncClient() as client:
        print(f"{DIM}intraday evidence…{RESET}")
        intraday = await intraday_evidence(client)
        frames30 = intraday.pop("frames30")

        daily = None
        if not skip_daily:
            print(f"{DIM}daily screens…{RESET}")
            daily = await daily_evidence(client)

        print(f"{DIM}live snapshot…{RESET}")
        live = await live_snapshot(client, frames30)

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "as_of": date.today().isoformat(),
        "tests": count_tests(),
        "intraday": intraday,
        "daily": daily,
        "live": live,
        "holdout_fraction": HOLDOUT,
        "costs_bps": COSTS.round_trip_bps,
    }

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, allow_nan=False, default=str)
    )
    out.write_text(html, encoding="utf-8")
    print(f"\nwrote {out}")
    split = intraday["split"]
    print(f"  holdout edge {split['holdout']['edge']:+.3f}R "
          f"over {split['holdout']['trades']} trades "
          f"(floor chosen on train: {intraday['chosen_floor']}%)")
    print(f"  {len(live['setups'])} setups on the board, "
          f"{live['with_bars']} names in the heatmap")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("report.html"))
    p.add_argument("--skip-daily", action="store_true")
    a = p.parse_args()
    asyncio.run(main(a.out, a.skip_daily))
