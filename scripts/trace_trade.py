"""One real trade, every step, and whether the repo agrees with itself.

    python scripts/trace_trade.py
    python scripts/trace_trade.py --symbol AAPL --which 0
    python scripts/trace_trade.py --store data/intraday

READ-ONLY. Places no order and touches no policy.

WHY THIS EXISTS
The published number is a BACKTEST number. It means something only if the
backtest models what the live path actually does. Every component here has its
own tests, and nothing tests the SEAM between them: the screen produces a
price, sizing turns it into shares, the adapter turns those into a bracket, and
`resolve` decides an outcome — using assumptions the broker never agreed to.

This walks one real setup through all four with the real functions, prints
every intermediate number, and then lists the places where the backtest assumes
something the live path does not guarantee. A divergence here does not mean the
code is broken; it means the backtest and reality disagree, and that the size
of the disagreement should be known rather than discovered later.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.alpaca import bracket_payload, round_tick
from scanner.execution.guards import RiskLimits
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.execution.sizing import size_position
from scanner.features import history
from scanner.features.intraday import SESSION_ALIGNED
from scanner.screens.smc import is_live_setup, scan

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

V = TradingPolicy()
LIMITS = RiskLimits()
EQUITY = 100_000.0


def rule(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}\n{DIM}{'-' * 68}{RESET}")


def check(ok: bool, label: str, detail: str = "") -> bool:
    mark = f"{GREEN}OK  {RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  {mark} {label}{('  ' + DIM + detail + RESET) if detail else ''}")
    return ok


def main(store: str, symbol: str | None, which: int) -> int:
    path = Path(store)
    history.STORE_DIR = path
    symbols = ([symbol] if symbol else
               sorted({f.name.split("__")[0] for f in path.glob("*.json")}))

    found = None
    for name in symbols:
        bars = history.accumulated(name, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        hits = [s for s in scan(bars, k=V.swing_k,
                                require_value_sweep=V.require_value_sweep,
                                min_risk_pct=V.min_risk_pct,
                                min_reward_risk=V.min_reward_risk)
                if not crosses_session(bars, s.trigger_index)]
        if len(hits) > which:
            found = (bars, hits[which])
            break
    if found is None:
        print(f"{RED}no setup found in {store}{RESET}")
        return 2
    bars, setup = found
    i = setup.trigger_index

    print(f"{BOLD}{setup.symbol}{RESET}  {store}  "
          f"{len(bars):,} bars of {V.interval_minutes}m")
    print(f"{DIM}policy {V.fingerprint}{RESET}")

    # ---------------------------------------------------------------- 1
    rule("1. THE PATTERN, as the screen found it")
    for label, index, price in (
        ("sweep low", setup.sweep_index, setup.sweep_price),
        ("higher low", setup.higher_low_index, setup.higher_low_price),
        ("CHoCH high", setup.choch_index, setup.choch_price),
        ("BOS / trigger", i, bars.highs[i]),
    ):
        print(f"  {label:<15}{bars.times[index]:%Y-%m-%d %H:%M}  "
              f"{price:>10.4f}   {DIM}bar {index}{RESET}")
    ok = True
    ok &= check(setup.sweep_index < setup.higher_low_index,
                "sweep precedes the higher low")
    ok &= check(setup.choch_index > setup.sweep_index,
                "CHoCH is after the sweep")
    ok &= check(i > setup.choch_index, "BOS is after the CHoCH")
    ok &= check(bars.highs[i] > setup.choch_price,
                "BOS bar takes out the CHoCH high",
                f"{bars.highs[i]:.4f} > {setup.choch_price:.4f}")
    ok &= check(setup.higher_low_price > setup.sweep_price,
                "the higher low is above the sweep")

    # ---------------------------------------------------------------- 2
    rule("2. THE THREE PRICES, and where each comes from")
    risk = setup.entry_price - setup.stop_price
    reward = setup.target_price - setup.entry_price
    print(f"  entry    {setup.entry_price:>10.4f}   {DIM}close of the BOS bar"
          f"{RESET}")
    print(f"  stop     {setup.stop_price:>10.4f}   {DIM}the higher low — a "
          f"level, not a percentage{RESET}")
    print(f"  target   {setup.target_price:>10.4f}   {DIM}highest swing high "
          f"before the sweep{RESET}")
    print(f"  risk     {risk:>10.4f}   "
          f"{risk / setup.entry_price * 100:>6.2f}% of entry")
    print(f"  reward   {reward:>10.4f}   R:R {reward / risk:>5.2f}")
    ok &= check(setup.entry_price == bars.closes[i],
                "entry is exactly the trigger bar's close")
    ok &= check(setup.stop_price == setup.higher_low_price,
                "stop is exactly the higher low")
    ok &= check(setup.stop_price < setup.entry_price < setup.target_price,
                "stop < entry < target")
    ok &= check(risk / setup.entry_price * 100 >= V.min_risk_pct,
                f"risk clears min_risk_pct {V.min_risk_pct}%")
    ok &= check(reward / risk >= V.min_reward_risk,
                f"R:R clears min_reward_risk {V.min_reward_risk}")

    # ---------------------------------------------------------------- 3
    rule("3. SIZING, with the real function")
    sizing = size_position(EQUITY, setup.entry_price, setup.stop_price)
    print(f"  equity   {EQUITY:>12,.2f}")
    print(f"  shares   {sizing.shares:>12,}   {DIM}limited by: "
          f"{sizing.limited_by or 'nothing'}{RESET}")
    print(f"  risk $   {sizing.risk_dollars:>12,.2f}   "
          f"{sizing.risk_dollars / EQUITY * 100:>5.3f}% of equity")
    print(f"  position {sizing.position_dollars:>12,.2f}   "
          f"{sizing.position_dollars / EQUITY * 100:>5.1f}% of equity")
    if sizing.shares:
        implied = sizing.shares * risk
        ok &= check(abs(implied - sizing.risk_dollars) < 0.01,
                    "risk dollars == shares x risk per share",
                    f"{implied:.2f}")
        ok &= check(sizing.risk_dollars <= EQUITY * 0.011,
                    "risk is at or under the 1% target",
                    f"{sizing.risk_dollars / EQUITY * 100:.3f}%")

    # ---------------------------------------------------------------- 4
    rule("4. THE ORDER, exactly as it would be sent")
    payload = bracket_payload(setup, sizing, datetime.now().astimezone())
    for key in ("symbol", "side", "qty", "type", "limit_price",
                "time_in_force", "order_class", "extended_hours"):
        print(f"  {key:<16}{payload.get(key)}")
    print(f"  take_profit     {payload['take_profit']}")
    print(f"  stop_loss       {payload['stop_loss']}")
    ok &= check(payload["order_class"] == "bracket",
                "one bracket, so the stop exists the moment the entry fills")
    ok &= check(payload["type"] == "limit",
                "LIMIT entry — a market order would not fill at the measured "
                "price")
    ok &= check(payload["time_in_force"] == "gtc",
                "GTC, so the stop survives overnight")
    ok &= check(payload["extended_hours"] is False,
                "regular hours only, matching the bars")
    entry_tick = round_tick(setup.entry_price)
    ok &= check(float(payload["limit_price"]) == round(entry_tick, 2),
                "limit price is the entry, tick-rounded",
                f"{setup.entry_price:.4f} -> {payload['limit_price']}")
    ok &= check(
        float(payload["stop_loss"]["stop_price"])
        < float(payload["limit_price"])
        < float(payload["take_profit"]["limit_price"]),
        "rounding did not invert the three prices")

    # ---------------------------------------------------------------- 5
    rule("5. THE EXIT, as the backtest resolves it")
    outcome = resolve(bars, symbol=setup.symbol, entry_index=i,
                      entry_price=setup.entry_price,
                      stop_price=setup.stop_price,
                      target_price=setup.target_price,
                      max_bars=V.max_hold_bars)
    if outcome is None:
        print(f"  {YELLOW}unresolved inside the series{RESET}")
    else:
        held = outcome.exit_index - i
        sessions = len({bars.times[j].date()
                        for j in range(i, outcome.exit_index + 1)})
        tint = GREEN if outcome.r_multiple > 0 else RED
        print(f"  reason   {outcome.reason}"
              f"{'  (GAPPED through)' if outcome.gapped else ''}")
        print(f"  exit     {outcome.exit_price:>10.4f}   "
              f"{bars.times[outcome.exit_index]:%Y-%m-%d %H:%M}")
        print(f"  held     {held:>10} bars   {sessions} session(s)")
        print(f"  result   {tint}{outcome.r_multiple:>+10.3f}R{RESET}   "
              f"{DIM}${outcome.r_multiple * sizing.risk_dollars:+,.2f} on this "
              f"size{RESET}")
        ok &= check(held <= V.max_hold_bars,
                    f"held within max_hold_bars ({V.max_hold_bars})")
        if outcome.reason == "stop" and not outcome.gapped:
            ok &= check(abs(outcome.r_multiple + 1.0) < 1e-9,
                        "a clean stop is exactly -1.000R")

    # ---------------------------------------------------------------- 6
    rule("6. WHERE THE BACKTEST AND THE LIVE PATH DISAGREE")
    print(f"  {YELLOW}These are not bugs. They are the size of the gap between"
          f"\n  a measured number and a real one.{RESET}\n")
    print(f"  {BOLD}1. The entry is assumed to fill. It is a LIMIT.{RESET}")
    print(f"     `resolve` starts every trade at the trigger close. Live, a GTC")
    print(f"     limit at {payload['limit_price']} fills only if price returns")
    print(f"     to it. A setup that gaps away is counted by the backtest and")
    print(f"     never entered live. Nothing here measures that rate.")
    print(f"\n  {BOLD}2. Intrabar order is unknown and settled against you."
          f"{RESET}")
    print(f"     When one 30m bar touches both levels, `resolve` takes the")
    print(f"     stop. A real bracket fills whichever price came first, which")
    print(f"     the bars cannot show. Pessimistic, so it understates.")
    print(f"\n  {BOLD}3. The Friday flatten is not modelled here.{RESET}")
    print(f"     `resolve` runs to stop, target or {V.max_hold_bars} bars. Live,"
          f"\n     flatten.py closes everything before the weekend — measured at")
    print(f"     -0.213R per trade, which is most of the edge.")
    print(f"\n  {BOLD}4. The timeout is a close, not an order.{RESET}")
    print(f"     `resolve` exits at the bar's close; live, manage_positions.py")
    print(f"     sends a market order at the next scan, up to 30 minutes later.")

    rule("VERDICT")
    if ok:
        print(f"  {GREEN}every invariant held on this trade{RESET}")
    else:
        print(f"  {RED}at least one invariant FAILED — see above{RESET}")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--symbol")
    parser.add_argument("--which", type=int, default=0,
                        help="which setup for that symbol, 0-based")
    args = parser.parse_args()
    raise SystemExit(main(args.store, args.symbol, args.which))
