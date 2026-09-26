"""Does the edge exist in the bars, or only in one provider's bars?

    python scripts/provider_compare.py
    python scripts/provider_compare.py --floor 20000000

READ-ONLY research. It reads both stores and prints.

WHY THIS IS THE TEST THAT MATTERS
Every number this project has published came from Yahoo bars — the +0.218R
baseline, the 20M liquidity floor at p=0.022, thirteen null ranking ideas, a
deflated Sharpe of 0.82. If the edge lives in Yahoo's particular
reconstruction of the tape rather than in the market, nothing in this
repository could currently tell.

Alpaca SIP is an independent feed of the same instrument, from the venue the
orders actually go to. Running the same screen over both is the closest thing
to replication available here, and it is free.

WHAT IS COMPARED, ON THE OVERLAPPING WINDOW ONLY
Comparing a 2-year Alpaca store against a 96-session Yahoo store would measure
the date range, not the provider. So both are cut to the dates they share
before anything is counted.

  setups      does the screen find the same structure in both?
  expectancy  does resolving them give the same answer?
  liquidity   how many names clear the floor in each?

THE FLOOR IS EXPECTED TO DISAGREE
Measured volumes were 0.87x on AAPL, 0.87x on KEY, 1.09x on CL and 1.49x on
VRTX — not a constant. So a name can sit on opposite sides of a fixed 20M line
depending on the feed, and KEY does. That is a recalibration, not a fault, and
the point of printing both columns is to size it.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history
from scanner.features.intraday import IntradayBars, SESSION_ALIGNED
from scanner.screens.smc import scan
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500, SP600

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,)}
V = TradingPolicy()
YAHOO = Path("data/intraday")
ALPACA = Path("data/intraday_alpaca")
MIN_PRICE = V.min_price   # the policy's, not a local copy


def load(store: Path, symbol: str) -> IntradayBars | None:
    original = history.STORE_DIR
    history.STORE_DIR = store
    try:
        return history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
    except Exception:                                    # noqa: BLE001
        return None
    finally:
        history.STORE_DIR = original


def clip(bars: IntradayBars, lo, hi) -> IntradayBars | None:
    """Cut to a shared date window so the comparison is about the provider."""
    keep = [i for i, t in enumerate(bars.times) if lo <= t.date() <= hi]
    if len(keep) < 60:
        return None
    return IntradayBars(
        symbol=bars.symbol, interval_minutes=bars.interval_minutes,
        alignment=bars.alignment,
        times=tuple(bars.times[i] for i in keep),
        opens=tuple(bars.opens[i] for i in keep),
        highs=tuple(bars.highs[i] for i in keep),
        lows=tuple(bars.lows[i] for i in keep),
        closes=tuple(bars.closes[i] for i in keep),
        volumes=tuple(bars.volumes[i] for i in keep),
    )


def dollar_volume(bars: IntradayBars, window: int = 20) -> float:
    closes, volumes = bars.closes[-window:], bars.volumes[-window:]
    if not closes:
        return 0.0
    return sum(v * c for v, c in zip(volumes, closes)) / len(closes)


def run(barsets: list[IntradayBars]) -> tuple[int, list[float]]:
    found, results = 0, []
    for bars in barsets:
        for setup in scan(bars, k=V.swing_k,
                          require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct,
                          min_reward_risk=V.min_reward_risk):
            if crosses_session(bars, setup.trigger_index):
                continue
            found += 1
            outcome = resolve(
                bars, symbol=setup.symbol, entry_index=setup.trigger_index,
                entry_price=setup.entry_price, stop_price=setup.stop_price,
                target_price=setup.target_price, max_bars=V.max_hold_bars,
            )
            if outcome:
                results.append(outcome.r_multiple)
    return found, results


async def main(index: str, floor: float) -> int:
    if not ALPACA.exists():
        print(f"{RED}{ALPACA} does not exist — run collect_alpaca.py first"
              f"{RESET}")
        return 2

    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])

    pairs = []
    for member in universe:
        y, a = load(YAHOO, member.symbol), load(ALPACA, member.symbol)
        if y is not None and a is not None:
            pairs.append((member.symbol, y, a))
    if not pairs:
        print(f"{YELLOW}no symbol is present in both stores yet{RESET}")
        return 0

    lo = max(min(y.times[0].date() for _, y, _ in pairs),
             min(a.times[0].date() for _, _, a in pairs))
    hi = min(max(y.times[-1].date() for _, y, _ in pairs),
             max(a.times[-1].date() for _, _, a in pairs))
    print(f"{DIM}{len(pairs)} symbol(s) in both stores{RESET}")
    print(f"{DIM}shared window {lo} -> {hi}{RESET}\n")

    ys, as_ = [], []
    liquid_y = liquid_a = 0
    ratios = []
    for _, y, a in pairs:
        cy, ca = clip(y, lo, hi), clip(a, lo, hi)
        if cy is None or ca is None:
            continue
        ys.append(cy)
        as_.append(ca)
        dy, da = dollar_volume(cy), dollar_volume(ca)
        if cy.closes[-1] >= MIN_PRICE and dy >= floor:
            liquid_y += 1
        if ca.closes[-1] >= MIN_PRICE and da >= floor:
            liquid_a += 1
        if dy > 0:
            ratios.append(da / dy)

    fy, ry = run(ys)
    fa, ra = run(as_)

    print(f"{BOLD}{'':<14}{'YAHOO':>14}{'ALPACA SIP':>14}{RESET}")
    print(f"  {'symbols':<12}{len(ys):>14}{len(as_):>14}")
    print(f"  {'bars':<12}{sum(len(b) for b in ys):>14,}"
          f"{sum(len(b) for b in as_):>14,}")
    print(f"  {'setups':<12}{fy:>14}{fa:>14}")
    print(f"  {'resolved':<12}{len(ry):>14}{len(ra):>14}")
    if ry and ra:
        my, ma = statistics.fmean(ry), statistics.fmean(ra)
        sy = statistics.stdev(ry) / len(ry) ** 0.5 if len(ry) > 1 else 0
        sa = statistics.stdev(ra) / len(ra) ** 0.5 if len(ra) > 1 else 0
        print(f"  {'mean R':<12}{my:>+14.3f}{ma:>+14.3f}")
        print(f"  {'se':<12}{sy:>14.3f}{sa:>14.3f}")
    print(f"  {'pass floor':<12}{liquid_y:>14}{liquid_a:>14}"
          f"   {DIM}(floor {floor:,.0f}){RESET}")
    if ratios:
        ratios.sort()
        print(f"\n{DIM}Alpaca/Yahoo dollar volume: median "
              f"{ratios[len(ratios)//2]:.2f}x, "
              f"range {ratios[0]:.2f}-{ratios[-1]:.2f}{RESET}")

    if ry and ra:
        gap = abs(statistics.fmean(ra) - statistics.fmean(ry))
        tol = 1.96 * max(sy, sa)
        print(f"\n{BOLD}READING IT{RESET}")
        if gap <= tol:
            print(f"  {GREEN}the two feeds agree within one standard error — "
                  f"the edge is in the market, not in a provider{RESET}")
        else:
            print(f"  {RED}the feeds disagree by more than a standard error "
                  f"({gap:.3f}R vs {tol:.3f}R). One of them is wrong about "
                  f"this instrument, and every published number came from "
                  f"Yahoo{RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--floor", type=float, default=V.min_dollar_volume)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.index, a.floor)))
