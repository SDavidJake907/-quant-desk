"""Does the model's take/skip beat taking every setup? Outcome hidden, then marked.

    python scripts/gemma_graded.py                         # since 2026-03-17
    python scripts/gemma_graded.py --since 2026-06-01 --limit 100
    python scripts/gemma_graded.py --report                # score what is saved

READ-ONLY research. Places no order and changes nothing live.

WHY THIS EXISTS
gemma_desk.py documented a `--graded` mode that was never built, and its live
path is not point-in-time: the daily trend came from today's closes and the
headlines from today's feed, so a historical setup would show the model the
future. This is that missing instrument, built point-in-time throughout:

    30m bars     bars.upto(trigger)                -- as gemma_desk does
    daily trend  daily closes strictly before the trigger's date
    headlines    data/news_history, created before the trigger, last 7 days

The prompt, schema and rules are gemma_desk's own, imported, so this measures
the decision the desk would make. Only setups that pass every live gate
(price, $20M dollar volume, stop, reward:risk, RS floor) are asked: live
never trades the rest, and letting the model skip them would credit it with
calls the system already makes. The mechanical baseline is "take all of
those"; the question is whether the model's skips remove more losers than
winners.

THE CONTAMINATION LIMIT
The model was trained on some past. On a setup from before its cutoff it may
recall the tape rather than read the data. The default window starts at
2026-03-17 -- the sector test's holdout -- to keep the sample recent. Treat
anything earlier as suspect.

RESUMABLE
Each decision is appended to logs/gemma_graded.jsonl as it is made. A rerun
skips setups already decided under the same model, so a pass stopped at the
03:30 TradingAgents slot loses nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.execution.sizing import size_position
from scanner.features import history, technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.features.intraday_context import compute as context_of
from scanner.llm.ollama import LLMError, chat_json
from scanner.screens.smc import scan

from gemma_desk import EQUITY, PROMPT, SCHEMA, render

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
LOG = Path("logs/gemma_graded.jsonl")
NEWS = Path("data/news_history")
NEWS_DAYS = 7


def headlines_before(symbol: str, moment: datetime) -> list[str]:
    """Headlines tagged with `symbol`, published in the week before `moment`."""
    found: list[tuple[str, str]] = []
    for back in range(NEWS_DAYS + 1):
        day = (moment - timedelta(days=back)).date()
        path = NEWS / f"{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue
            if symbol in (row.get("symbols") or []) and stamp < moment:
                found.append((row["created_at"], row.get("headline", "")))
    seen, out = set(), []
    for _, title in sorted(found, reverse=True):          # newest first, deduped
        if title and title not in seen:
            seen.add(title)
            out.append(title)
    return out


def daily_before(series, when: date):
    """Daily history as it stood the morning of `when`, or None if too short."""
    cut = sum(1 for d in series.dates if d < when)
    return series.upto(cut - 1) if cut > 260 else None


def gates_for(bars, setup, strength):
    risk = setup.entry_price - setup.stop_price
    volume = sum(v * c for v, c in zip(bars.volumes[-20:], bars.closes[-20:])) / 20
    return [
        ("price >= $5", bars.closes[-1] >= V.min_price, f"{bars.closes[-1]:.2f}"),
        ("dollar volume >= 20M", volume >= V.min_dollar_volume, f"${volume/1e6:.1f}M/bar"),
        ("stop >= 1.5%", risk / setup.entry_price * 100 >= V.min_risk_pct,
         f"{risk/setup.entry_price*100:.2f}%"),
        ("reward:risk >= 1.5",
         (setup.target_price - setup.entry_price) / risk >= V.min_reward_risk,
         f"{(setup.target_price-setup.entry_price)/risk:.2f}"),
        ("relative strength floor",
         strength is None or strength >= V.min_relative_strength,
         f"{strength:+.1f}pp vs {V.min_relative_strength:+.1f}"
         if strength is not None else "unknown"),
    ]


def setup_key(symbol: str, trigger: datetime) -> str:
    return f"{symbol}@{trigger.isoformat()}"


def done_keys(model: str) -> set[str]:
    if not LOG.exists():
        return set()
    keys = set()
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("model") == model:
            keys.add(row["key"])
    return keys


def collect(store: Path, since: date):
    """Every Mon-Thu setup since `since`, oldest first, with its outcome."""
    history.STORE_DIR = store
    found = []
    for symbol in sorted({f.name.split("__")[0] for f in store.glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        for setup in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                          min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = setup.trigger_index
            when = bars.times[i]
            if when.date() < since or crosses_session(bars, i):
                continue
            if setup.entry_price - setup.stop_price <= 0:
                continue
            if when.weekday() >= V.wind_down_from_weekday:
                continue
            outcome = resolve(bars, symbol=setup.symbol, entry_index=i,
                              entry_price=setup.entry_price, stop_price=setup.stop_price,
                              target_price=setup.target_price, max_bars=V.max_hold_bars)
            if outcome:
                found.append((when, bars, setup, outcome.r_multiple))
    found.sort(key=lambda row: row[0])
    return found


async def decide(rows, model: str, limit: int, until: datetime | None = None) -> None:
    skip = done_keys(model)
    todo = [r for r in rows if setup_key(r[1].symbol, r[0]) not in skip][:limit]
    print(f"{DIM}{len(rows)} setups in window, {len(skip)} already decided by "
          f"{model}, deciding {len(todo)}{RESET}")
    if not todo:
        return
    names = tuple(sorted({b.symbol for _, b, _, _ in todo}))
    async with httpx.AsyncClient(timeout=600) as client:
        benchmark = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(names, client, range_="5y")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        gated = 0
        for n, (when, bars, setup, r) in enumerate(todo, 1):
            if until is not None and datetime.now() >= until:
                print(f"{YELLOW}stop time {until:%H:%M} reached after {n - 1}; "
                      f"rerun to resume{RESET}")
                break
            symbol = bars.symbol
            ctx = context_of(bars.upto(setup.trigger_index))
            past = bars.upto(setup.trigger_index)
            day = when.date()
            facts, strength = {}, None
            series, bench = daily.get(symbol), daily_before(benchmark, day)
            stock = daily_before(series, day) if series is not None else None
            if stock is not None and bench is not None:
                f, b = technicals.compute(stock), technicals.compute(bench)
                strength = technicals.relative_strength(f, b)
                if strength is not None:
                    facts["12-1 vs S&P (pp)"] = f"{strength:+.1f}"
                if f.sma200:
                    facts["vs 200-day (%)"] = f"{(stock.closes[-1] / f.sma200 - 1) * 100:+.1f}"
                if f.pct_from_52w_high is not None:
                    facts["from 52w high (%)"] = f"{f.pct_from_52w_high:+.1f}"
            moment = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            news = headlines_before(symbol, moment)
            gates = gates_for(past, setup, strength)
            if not all(ok for _, ok, _ in gates):
                gated += 1          # live never trades it; asking would flatter the skips
                continue
            sizing = size_position(EQUITY, setup.entry_price, setup.stop_price)
            block = render(symbol, setup, past, ctx, facts, gates, sizing, news)
            try:
                out = await chat_json(PROMPT.format(rules=V.as_prose(), block=block),
                                      SCHEMA, client, model=model)
            except LLMError as exc:
                print(f"  {YELLOW}{symbol} {when:%Y-%m-%d %H:%M}: model failed: {exc}{RESET}")
                continue
            row = {"key": setup_key(symbol, when), "model": model, "symbol": symbol,
                   "trigger": when.isoformat(), "decision": out.get("decision"),
                   "conviction": out.get("conviction"), "why": out.get("why", "")[:200],
                   "headlines": len(news), "r": r, "policy": V.fingerprint}
            with LOG.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            if n % 25 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} seen, {gated} failed a live gate", flush=True)
        print(f"{DIM}{gated} setup(s) failed a live gate and were not asked{RESET}")


def permutation_p(a: list[float], b: list[float], draws: int = 20_000) -> float:
    if len(a) < 5 or len(b) < 5:
        return 1.0
    observed = abs(statistics.fmean(a) - statistics.fmean(b))
    pool, cut, rng, hits = a + b, len(a), random.Random(0), 0
    for _ in range(draws):
        rng.shuffle(pool)
        if abs(statistics.fmean(pool[:cut]) - statistics.fmean(pool[cut:])) >= observed:
            hits += 1
    return hits / draws


def line(label: str, values: list[float]) -> None:
    if not values:
        print(f"    {label:<22}n=0")
        return
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
    win = sum(v > 0 for v in values) / len(values) * 100
    tint = GREEN if mean > 0 else RED
    print(f"    {label:<22}{tint}{mean:>+7.3f}R{RESET}  se {se:>5.3f}  "
          f"n={len(values):<4} win {win:>3.0f}%  total {sum(values):+.1f}R")


def report(model: str, since: date) -> int:
    rows = []
    for text in (LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []):
        try:
            row = json.loads(text)
        except ValueError:
            continue
        if row.get("model") == model and date.fromisoformat(row["trigger"][:10]) >= since:
            rows.append(row)
    if not rows:
        print(f"{YELLOW}nothing decided yet for {model}{RESET}")
        return 0
    every = [r["r"] for r in rows]
    taken = [r["r"] for r in rows if r["decision"] == "take"]
    skipped = [r["r"] for r in rows if r["decision"] == "skip"]
    print(f"\n{BOLD}GRADED  {model}  since {since}  ({len(rows)} setups, outcome "
          f"hidden at decision time){RESET}")
    line("mechanical: take all", every)
    line("model: take", taken)
    line("model: skip", skipped)
    by_conv = defaultdict(list)
    for r in rows:
        if r["decision"] == "take":
            by_conv[r.get("conviction") or "?"].append(r["r"])
    for conviction in ("strong", "neutral", "weak"):
        if by_conv.get(conviction):
            line(f"  take, {conviction}", by_conv[conviction])
    with_news = sum(1 for r in rows if r.get("headlines"))
    print(f"    {DIM}{with_news}/{len(rows)} decisions had point-in-time headlines{RESET}")
    print(f"\n{BOLD}DID THE SKIPS HELP?{RESET}")
    if len(taken) < 10 or len(skipped) < 10:
        print(f"  {YELLOW}too few on one side ({len(taken)} take / {len(skipped)} skip){RESET}")
        return 0
    gap = statistics.fmean(taken) - statistics.fmean(skipped)
    p = permutation_p(list(taken), list(skipped))
    kept = sum(taken) - sum(every)
    print(f"  take minus skip {gap:+.3f}R per trade, p={p:.3f}")
    print(f"  total R if only 'take' were traded: {sum(taken):+.1f}R vs {sum(every):+.1f}R "
          f"taking all ({kept:+.1f}R)")
    if gap > 0 and p < 0.05:
        print(f"  {GREEN}the model's skips removed losers -- a lead, to be confirmed "
              f"live before it gates anything{RESET}")
    elif gap > 0:
        print(f"  {DIM}right direction, not distinguishable from chance{RESET}")
    else:
        print(f"  {RED}the skips removed winners at least as often as losers{RESET}")
    return 0


async def main(args) -> int:
    since = date.fromisoformat(args.since)
    if not args.report:
        rows = collect(Path(args.store), since)
        until = None
        if args.until:
            hour, minute = map(int, args.until.split(":"))
            until = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            if until <= datetime.now():
                until += timedelta(days=1)
        await decide(rows, args.model, args.limit, until)
    return report(args.model, since)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default="data/intraday_alpaca")
    parser.add_argument("--since", default="2026-03-17")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--model", default=settings.triage_model)
    parser.add_argument("--report", action="store_true", help="score saved decisions only")
    parser.add_argument("--until", default="",
                        help="HH:MM local; stop deciding then (e.g. 03:15, before TradingAgents)")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
