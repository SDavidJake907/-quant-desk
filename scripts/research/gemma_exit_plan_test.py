"""Should each position get its own exit plan? The model picks from a menu at entry.

    python scripts/gemma_exit_plan_test.py
    python scripts/gemma_exit_plan_test.py --report

READ-ONLY research. Places no order and changes nothing live.

THE IDEA (2026-10-04)
The levels already differ per stock -- the stop is that setup's higher low, the
target its range high. What is uniform is the MANAGEMENT: one stop, one target,
60 bars, flat by Friday. The proposal: decide the management per position, at
entry, from what the stock looks like.

HOW IT IS MEASURED
Free-text exit plans cannot be scored, so the model chooses one of five
MECHANICAL plans, each fully simulated on the same bars:

    standard     stop at the higher low, target at the range high, 60 bars
    quick        same stop, target cut to 1.5R when the range high is further
    breakeven    standard, stop moved to entry once the trade reaches +1R
    runner       no fixed target; stop to entry at +1R; out at the cap or Friday
    short_clock  standard, but out at the close of bar 20 if nothing has fired

Three readings, so the answer cannot hide:
    1. every plan applied to EVERY trade -- does any one beat standard?
    2. the model's per-trade pick -- does choosing beat the best single plan?
    3. the hindsight ceiling -- the best plan per trade, which no one can know
       at entry; it bounds what choosing could ever add.

POINT-IN-TIME, as gemma_graded.py: 30m bars to the trigger, daily closes
before its date, headlines created before it. Only setups that pass every
live gate are asked.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from datetime import date, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.execution.sizing import size_position
from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday_context import compute as context_of
from scanner.llm.ollama import LLMError, chat_json

from gemma_desk import EQUITY, render
from gemma_graded import collect, daily_before, gates_for, headlines_before, line, setup_key

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
LOG = Path("logs/gemma_exit_plan.jsonl")
PLANS = ("standard", "quick", "breakeven", "runner", "short_clock")
QUICK_R, BREAKEVEN_AT, SHORT_BARS = 1.5, 1.0, 20

SCHEMA = {
    "type": "object",
    "properties": {
        "plan": {"type": "string", "enum": list(PLANS)},
        "why": {"type": "string"},
    },
    "required": ["plan", "why"],
}

PROMPT = """A mechanical screen found this setup and every live filter passed. The
entry, stop and target are fixed by the structure. Your only job is to choose HOW
THE POSITION IS MANAGED, from five plans. The stop never widens in any of them.

  standard     hold for the stop (the higher low) or the target (the range high),
               up to {cap} bars; closed before the weekend
  quick        same stop, target cut to {quick}R -- for a move unlikely to run far
  breakeven    standard, but the stop moves to entry once the trade is +{be}R --
               for a move that may stall after a first push
  runner       no fixed target; stop to entry at +{be}R; held to the time limit
               or the weekend -- for a move that could trend well past the range
  short_clock  standard, but closed after {short} bars if nothing has fired --
               for a setup that should work quickly or not at all

What this strategy has measured about itself: about 60% of trades stop out and
the money comes from the 25% that reach target. Choose the plan that fits THIS
stock's picture, and say which fact decided it.

plan: one of the five.
why: one sentence naming the deciding fact from the data shown.

{block}
"""


def simulate(bars, setup, plan: str) -> float:
    """R for one plan. Same bar logic as mfe_trail_test.walk's baseline."""
    entry, stop = setup.entry_price, setup.stop_price
    risk = entry - stop
    target = setup.target_price
    if plan == "quick":
        target = min(target, entry + QUICK_R * risk)
    elif plan == "runner":
        target = float("inf")
    first = setup.trigger_index
    cap = SHORT_BARS if plan == "short_clock" else V.max_hold_bars
    last = min(first + cap, len(bars) - 1)
    live_stop, peak = stop, entry
    for i in range(first + 1, last + 1):
        o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
        if o <= live_stop:
            return (o - entry) / risk
        if o >= target:
            return (o - entry) / risk
        if lo <= live_stop:
            return (live_stop - entry) / risk
        if h >= target:
            return (target - entry) / risk
        peak = max(peak, h)
        if plan in ("breakeven", "runner") and (peak - entry) / risk >= BREAKEVEN_AT:
            live_stop = max(live_stop, entry)
        if bars.times[i].weekday() == 4:
            nxt = i + 1
            if nxt > last or bars.times[nxt].date() != bars.times[i].date():
                return (bars.closes[i] - entry) / risk
    return (bars.closes[last] - entry) / risk


async def decide(args) -> None:
    rows = collect(Path(args.store), date.fromisoformat(args.since))
    done = set()
    if LOG.exists():
        done = {json.loads(t)["key"] for t in LOG.read_text(encoding="utf-8").splitlines()
                if t.strip() and json.loads(t).get("model") == args.model}
    names = tuple(sorted({b.symbol for _, b, _, _ in rows}))
    asked = gated = 0
    async with httpx.AsyncClient(timeout=600) as client:
        benchmark = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(names, client, range_="5y")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        for when, bars, setup, _ in rows:
            key = setup_key(bars.symbol, when)
            if key in done:
                continue
            past = bars.upto(setup.trigger_index)
            series, facts, strength = daily.get(bars.symbol), {}, None
            stock = daily_before(series, when.date()) if series is not None else None
            base = daily_before(benchmark, when.date())
            if stock is not None and base is not None:
                f = technicals.compute(stock)
                strength = technicals.relative_strength(f, technicals.compute(base))
                if strength is not None:
                    facts["12-1 vs S&P (pp)"] = f"{strength:+.1f}"
                if f.sma200:
                    facts["vs 200-day (%)"] = f"{(stock.closes[-1] / f.sma200 - 1) * 100:+.1f}"
                if f.pct_from_52w_high is not None:
                    facts["from 52w high (%)"] = f"{f.pct_from_52w_high:+.1f}"
            gates = gates_for(past, setup, strength)
            if not all(ok for _, ok, _ in gates):
                gated += 1
                continue
            moment = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            block = render(bars.symbol, setup, past, context_of(past), facts, gates,
                           size_position(EQUITY, setup.entry_price, setup.stop_price),
                           headlines_before(bars.symbol, moment))
            prompt = PROMPT.format(cap=V.max_hold_bars, quick=QUICK_R, be=BREAKEVEN_AT,
                                   short=SHORT_BARS, block=block)
            try:
                out = await chat_json(prompt, SCHEMA, client, model=args.model)
            except LLMError as exc:
                print(f"  {YELLOW}{bars.symbol}: model failed: {exc}{RESET}")
                continue
            row = {"key": key, "model": args.model, "symbol": bars.symbol,
                   "trigger": when.isoformat(), "pick": out.get("plan"),
                   "why": str(out.get("why", ""))[:200],
                   "r": {p: simulate(bars, setup, p) for p in PLANS}}
            with LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            asked += 1
            if asked % 25 == 0:
                print(f"  {asked} decided", flush=True)
    print(f"{DIM}{asked} decided, {gated} failed a live gate and were not asked{RESET}")


def paired_p(diffs: list[float], draws: int = 20_000) -> float:
    if len(diffs) < 5:
        return 1.0
    observed, rng, hits = abs(statistics.fmean(diffs)), random.Random(0), 0
    for _ in range(draws):
        if abs(statistics.fmean([d if rng.random() < 0.5 else -d for d in diffs])) >= observed:
            hits += 1
    return hits / draws


def report(model: str) -> int:
    rows = [json.loads(t) for t in (LOG.read_text(encoding="utf-8").splitlines()
                                    if LOG.exists() else []) if t.strip()]
    rows = [r for r in rows if r.get("model") == model and r.get("pick") in PLANS]
    if not rows:
        print(f"{YELLOW}nothing decided yet for {model}{RESET}")
        return 0
    print(f"\n{BOLD}PER-POSITION EXIT PLAN  {model}  {len(rows)} setups{RESET}")
    print(f"  {BOLD}1. one plan for every trade{RESET}")
    for plan in PLANS:
        line(plan, [r["r"][plan] for r in rows])
    picked = [r["r"][r["pick"]] for r in rows]
    best_fixed = max(PLANS, key=lambda p: statistics.fmean(r["r"][p] for r in rows))
    print(f"\n  {BOLD}2. the model's pick per trade{RESET}")
    line("model's choice", picked)
    mix = {p: sum(r["pick"] == p for r in rows) for p in PLANS}
    print(f"    {DIM}picks: " + ", ".join(f"{p} {n}" for p, n in mix.items() if n) + RESET)
    for name, ref in (("standard", "standard"), (f"best single ({best_fixed})", best_fixed)):
        diffs = [r["r"][r["pick"]] - r["r"][ref] for r in rows]
        gap, p = statistics.fmean(diffs), paired_p(diffs)
        tint = GREEN if gap > 0 and p < 0.05 else RED if gap < 0 else DIM
        print(f"    vs {name:<26}{tint}{gap:+.3f}R per trade{RESET}, total "
              f"{sum(diffs):+.1f}R, paired p={p:.3f}")
    print(f"\n  {BOLD}3. hindsight ceiling{RESET}  {DIM}(best plan per trade, "
          f"unknowable at entry){RESET}")
    line("oracle", [max(r["r"].values()) for r in rows])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--since", default="2026-03-17")
    parser.add_argument("--model", default=settings.triage_model)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.report:
        asyncio.run(decide(args))
    return report(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
