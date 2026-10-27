"""Can the model manage the exit better than the bracket? Bar by bar, outcome hidden.

    python scripts/gemma_exit_graded.py --sample 100 --until 03:15
    python scripts/gemma_exit_graded.py --report

READ-ONLY research. Places no order and changes nothing live.

THE QUESTION
Live, the model reviews every open position every scan and says hold or
exit_early -- recorded, never acted on, and 118 of 118 calls said hold. This
replays historical trades and lets it act: at every CHECK-th bar after entry
it is shown the position as it stood at that bar's close, and an exit_early
closes the trade at the next bar's open.

THE BRACKET STAYS
Stop, target, the Friday flatten and the 60-bar cap all still fire. The model
can only leave EARLY -- never widen the stop. That is the only version that
could ever be allowed live.

TWO ARMS, ONE BASELINE
    baseline   the bracket alone                     what runs today
    A  live    review_positions' own prompt + block  what the model sees now:
               entry, price, P&L, indicators, daily trend, headlines. It is
               NOT told the stop, the target, or how long the trade has run.
    B  full    the same prompt, block enriched with stop, target, open R, best
               R so far, bars held and left, every indicator the scanner
               computes, and swing structure since entry (confirmed pivots only)
Both arms answer the same question with the same schema.

POINT-IN-TIME at every check: bars.upto(i), daily closes before bar i's date,
headlines created before bar i. The live prompt's 24h activity and learned
memory cannot be rebuilt honestly for a past date, so arm A receives a neutral
"not available in a replay" in their place.

SAMPLE
--sample N draws N of the gated setups since --since with a fixed seed, so a
resumed or repeated run asks about the same trades.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.features import technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday_context import compute as context_of
from scanner.features.structure import HIGH, LOW, known_at, swings
from scanner.llm.ollama import LLMError, chat_json

from gemma_graded import collect, daily_before, gates_for, headlines_before, line, setup_key
from review_positions import PROMPT, SCHEMA, block_for

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
LOG = Path("logs/gemma_exit_graded.jsonl")
CHECK = 2            # bars between reviews: hourly on 30m bars
SEED = 20261004
REPLAY = "Not available in a replay: this is a historical trade."


def daily_facts(series, bench, day: date) -> dict[str, str]:
    stock = daily_before(series, day) if series is not None else None
    base = daily_before(bench, day)
    if stock is None or base is None:
        return {}
    f = technicals.compute(stock)
    facts = {}
    strength = technicals.relative_strength(f, technicals.compute(base))
    if strength is not None:
        facts["12-1 vs S&P (pp)"] = f"{strength:+.1f}"
    if f.sma200:
        facts["vs 200-day (%)"] = f"{(stock.closes[-1] / f.sma200 - 1) * 100:+.1f}"
    if f.pct_from_52w_high is not None:
        facts["from 52w high (%)"] = f"{f.pct_from_52w_high:+.1f}"
    return facts


def structure_lines(bars, first: int, i: int) -> list[str]:
    """Confirmed pivots since entry, as a trader on bar i could have seen them."""
    seen = known_at(swings(bars.highs[:i + 1], bars.lows[:i + 1], V.swing_k), i, V.swing_k)
    lows = [s.price for s in seen if s.kind == LOW and s.index > first]
    highs = [s.price for s in seen if s.kind == HIGH and s.index > first]
    out = [f"  confirmed swing lows since entry   {len(lows)}"
           + (f", last {lows[-1]:.2f}" if lows else ""),
           f"  confirmed swing highs since entry  {len(highs)}"
           + (f", last {highs[-1]:.2f}" if highs else "")]
    if len(lows) >= 2:
        out.append(f"  latest low vs prior low            "
                   f"{'HIGHER (uptrend intact)' if lows[-1] > lows[-2] else 'LOWER (structure broken)'}")
    if len(highs) >= 2:
        out.append(f"  latest high vs prior high          "
                   f"{'HIGHER' if highs[-1] > highs[-2] else 'LOWER'}")
    return out


def block_full(symbol, setup, bars, first, i, ctx, daily, headlines, peak) -> str:
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    now = bars.closes[i]
    lines = [f"[{symbol}]", "", "THE POSITION",
             f"  entry                 {entry:.2f}",
             f"  stop (higher low)     {stop:.2f}   -- fires automatically",
             f"  target (range high)   {target:.2f}   -- fires automatically",
             f"  now                   {now:.2f}   open {(now - entry) / risk:+.2f}R",
             f"  best so far           {(peak - entry) / risk:+.2f}R",
             f"  bars held             {i - first} of {V.max_hold_bars}  "
             f"({V.max_hold_bars - (i - first)} left before the time exit)",
             "", "30-MINUTE INDICATORS"]
    for name in ("rsi14", "macd", "macd_signal", "macd_histogram", "atr_pct",
                 "volume_ratio", "range_tightness", "ema50_slope_pct"):
        value = getattr(ctx, name, None)
        if value is not None:
            lines.append(f"  {name:<21}{value:>10.3f}")
    for name in ("ema9", "ema21", "ema50", "ema200"):
        value = getattr(ctx, name, None)
        if value:
            lines.append(f"  {name:<21}{value:>10.2f}   price "
                         f"{'above' if ctx.price >= value else 'below'}")
    lines += ["", "STRUCTURE SINCE ENTRY (confirmed pivots only)"]
    lines += structure_lines(bars, first, i)
    if daily:
        lines += ["", "DAILY TREND"] + [f"  {k:<21}{v:>10}" for k, v in daily.items()]
    lines += ["", "RECENT HEADLINES"] + ([f"  - {h}" for h in headlines[:5]] or ["  (none)"])
    return "\n".join(lines)


def block_live(symbol, setup, bars, i, ctx, daily, headlines) -> str:
    entry, now = setup.entry_price, bars.closes[i]
    position = {"qty": "100", "avg_entry_price": entry, "current_price": now,
                "unrealized_pl": (now - entry) * 100}
    return block_for(symbol, position, bars, ctx, daily, headlines)


def bracket_step(bars, i, entry, stop, target, last):
    """Mirror of mfe_trail_test.walk's baseline. (R, reason) if bar i exits."""
    risk = entry - stop
    o, h, lo = bars.opens[i], bars.highs[i], bars.lows[i]
    if o <= stop:
        return (o - entry) / risk, "stop"
    if o >= target:
        return (o - entry) / risk, "target"
    if lo <= stop:
        return (stop - entry) / risk, "stop"
    if h >= target:
        return (target - entry) / risk, "target"
    if bars.times[i].weekday() == 4:
        nxt = i + 1
        if nxt > last or bars.times[nxt].date() != bars.times[i].date():
            return (bars.closes[i] - entry) / risk, "flattened"
    if i == last:
        return (bars.closes[i] - entry) / risk, "cap"
    return None


async def walk(arm, bars, setup, client, model, series, bench, daily_cache):
    """Returns (R, reason, checks). arm is None (baseline), 'A' or 'B'."""
    entry, stop, target = setup.entry_price, setup.stop_price, setup.target_price
    risk = entry - stop
    first = setup.trigger_index
    last = min(first + V.max_hold_bars, len(bars) - 1)
    peak, checks = entry, 0
    for i in range(first + 1, last + 1):
        done = bracket_step(bars, i, entry, stop, target, last)
        if done:
            return done[0], done[1], checks
        peak = max(peak, bars.highs[i])
        if arm is None or (i - first) % CHECK or i + 1 > last:
            continue
        day = bars.times[i].date()
        if day not in daily_cache:
            daily_cache[day] = daily_facts(series, bench, day)
        moment = bars.times[i] if bars.times[i].tzinfo else bars.times[i].replace(tzinfo=timezone.utc)
        news = headlines_before(setup.symbol, moment + timedelta(minutes=V.interval_minutes))
        ctx = context_of(bars.upto(i))
        if arm == "A":
            block = block_live(setup.symbol, setup, bars, i, ctx, daily_cache[day], news)
        else:
            block = block_full(setup.symbol, setup, bars, first, i, ctx,
                               daily_cache[day], news, peak)
        prompt = PROMPT.format(rules=V.as_prose(), recent=REPLAY, memory=REPLAY, block=block)
        checks += 1
        try:
            out = await chat_json(prompt, SCHEMA, client, model=model)
        except LLMError:
            continue                       # a failed review is a hold, as live
        if out.get("call") == "exit_early":
            return (bars.opens[i + 1] - entry) / risk, f"model@{i - first}", checks
    return (bars.closes[last] - entry) / risk, "cap", checks


def done_keys(model):
    if not LOG.exists():
        return set()
    return {json.loads(t)["key"] for t in LOG.read_text(encoding="utf-8").splitlines()
            if t.strip() and json.loads(t).get("model") == model}


def gated(rows, benchmark, daily):
    """Only setups every live gate would pass, as gemma_graded asks."""
    out = []
    for when, bars, setup, r in rows:
        past = bars.upto(setup.trigger_index)
        series, strength = daily.get(bars.symbol), None
        stock, base = (daily_before(series, when.date()) if series is not None else None,
                       daily_before(benchmark, when.date()))
        if stock is not None and base is not None:
            strength = technicals.relative_strength(technicals.compute(stock),
                                                    technicals.compute(base))
        if all(ok for _, ok, _ in gates_for(past, setup, strength)):
            out.append((when, bars, setup, r))
    return out


async def run(args) -> None:
    rows = collect(Path(args.store), date.fromisoformat(args.since))
    names = tuple(sorted({b.symbol for _, b, _, _ in rows}))
    async with httpx.AsyncClient(timeout=600) as client:
        benchmark = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(names, client, range_="5y")
        pool = gated(rows, benchmark, daily)
        sample = random.Random(SEED).sample(pool, min(args.sample, len(pool)))
        sample.sort(key=lambda row: row[0])
        skip = done_keys(args.model)
        todo = [row for row in sample if setup_key(row[1].symbol, row[0]) not in skip]
        print(f"{DIM}{len(rows)} setups, {len(pool)} pass the live gates, sample "
              f"{len(sample)}, {len(sample) - len(todo)} already done{RESET}", flush=True)
        until = None
        if args.until:
            hh, mm = map(int, args.until.split(":"))
            until = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            if until <= datetime.now():
                until += timedelta(days=1)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        for n, (when, bars, setup, _) in enumerate(todo, 1):
            if until and datetime.now() >= until:
                print(f"{YELLOW}stop time reached after {n - 1}; rerun to resume{RESET}")
                break
            cache: dict = {}
            series = daily.get(bars.symbol)
            base = await walk(None, bars, setup, client, args.model, series, benchmark, cache)
            a = await walk("A", bars, setup, client, args.model, series, benchmark, cache)
            b = await walk("B", bars, setup, client, args.model, series, benchmark, cache)
            row = {"key": setup_key(bars.symbol, when), "model": args.model,
                   "symbol": bars.symbol, "trigger": when.isoformat(),
                   "base": base[0], "base_exit": base[1],
                   "a": a[0], "a_exit": a[1], "a_checks": a[2],
                   "b": b[0], "b_exit": b[1], "b_checks": b[2]}
            with LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            if n % 10 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} trades replayed", flush=True)


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
    rows = [r for r in rows if r.get("model") == model]
    if not rows:
        print(f"{YELLOW}nothing replayed yet for {model}{RESET}")
        return 0
    print(f"\n{BOLD}EXIT MANAGEMENT  {model}  {len(rows)} trades, bracket kept, "
          f"review every {CHECK} bars{RESET}")
    line("baseline: bracket", [r["base"] for r in rows])
    for arm, label in (("a", "A: live prompt"), ("b", "B: full picture")):
        line(label, [r[arm] for r in rows])
    for arm, label in (("a", "A: live prompt"), ("b", "B: full picture")):
        early = [r for r in rows if str(r[f"{arm}_exit"]).startswith("model")]
        diffs = [r[arm] - r["base"] for r in rows]
        helped = sum(1 for r in early if r[arm] > r["base"])
        checks = sum(r[f"{arm}_checks"] for r in rows)
        print(f"\n  {BOLD}{label}{RESET}: exited early on {len(early)}/{len(rows)} trades "
              f"({checks} reviews); early exit beat holding on {helped}/{len(early) or 1}")
        p = paired_p(diffs)
        gap = statistics.fmean(diffs)
        tint = GREEN if gap > 0 and p < 0.05 else RED if gap < 0 else DIM
        print(f"    vs bracket {tint}{gap:+.3f}R per trade{RESET}, total "
              f"{sum(diffs):+.1f}R, paired p={p:.3f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--since", default="2026-03-17")
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--model", default=settings.triage_model)
    parser.add_argument("--until", default="")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.report:
        asyncio.run(run(args))
    return report(args.model)


if __name__ == "__main__":
    raise SystemExit(main())
