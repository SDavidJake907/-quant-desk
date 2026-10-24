"""Gemma's research lab session (operator 2026-10-23: "let her run backtests, maybe she
will find a strategy that works"). Rules in scanner/lab/.

    python scripts/gemma_lab.py              # the Saturday session: 15 recipes + one nomination
    python scripts/gemma_lab.py --tries 3    # a shorter session
    python scripts/gemma_lab.py --check      # load the data, run one fixed recipe, ask nobody

She writes a recipe from the menu, it runs as a whole book on the SEARCH years
with the random control beside it, she sees the result and writes the next.
At the end she may nominate ONE searched recipe for the hidden years: stage A
(2022-23) first, stage B (2024-today) only if A passes her bar. A recipe that
passes both is reported to the operator as a candidate paper book. Nothing
here trades. Every run is logged in data/lab/log.jsonl and counts as a trial.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.lab import indicators, ledger, recipe as rc, sim
from scanner.llm.ollama import LLMError, chat_json
from scanner.sources import composite, index_history
from scanner.sources.index_history import SP500

SEARCH = (date(2007, 7, 1), date(2021, 12, 31))
HIDDEN_A = (date(2022, 1, 1), date(2023, 12, 31))
HIDDEN_B_START = date(2024, 1, 1)
TRIES = 15                     # Gemma asked for 15 (2026-10-23)
CHUNK = 100


CHECK_RECIPE = {"filters": [{"name": "price_vs_sma200_pct", "op": "above", "value": 0},
                            {"name": "market_uptrend", "op": "is", "value": 1}],
                "rank_by": "mom_12_1_pct", "rank_order": "high_first", "top_n": 10,
                "rebalance_days": 21, "stop_atr": 0.0, "target_r": 0.0, "max_hold_days": 63,
                "max_per_sector": 3}

INTRO = """You are in your RESEARCH LAB (Saturday; markets closed). You design swing-trading strategies
as recipes; the system backtests each one as a whole book and shows you the result. Nothing here trades.

How a recipe is run: S&P 500 members as of each date (companies that later left are included where
price data exists). Every `rebalance_days` trading days, on the close, the empty slots of a `top_n`-slot
book are filled with the best-ranked members passing ALL filters; the buy is at the NEXT day's close;
each slot is 1/top_n of the book. Exits from the next day: stop and target (a day that opens through
a level fills at the open; a day touching both is a stop), then the time limit at the close. Costs are
included. Each result is shown beside RANDOM picks run through the same book and exits, and beside the
S&P 500. If you cannot beat random picks, the idea is not adding anything.

Search years: {s0} to {s1}. The years after are HIDDEN from you. At the end of the session you may
nominate ONE recipe for them: 2022-23 first, then 2024-today only if it passes. Your bar, on each:
beat the S&P 500 by {pts:g} points a year, Sharpe above {sh:g}, worst drawdown {dd:g}% or less -- and
beat random picks with at least {mt} trades. With enough tries one recipe looks good by luck; every
try is counted, so make each one a real idea, not a small tweak of a lucky number.

THE MENU
{menu}"""


def say(text: str) -> None:
    print(text, flush=True)


async def load(client) -> tuple[dict, list, dict, callable, dict, int]:
    """(stocks, calendar, spx closes, members, uptrend, missing count)."""
    history = await index_history.load(client)
    symbols = sorted(history.symbols_ever(date(2006, 1, 1), date.today(), collapse_share_classes=True))
    sectors = {m.symbol: m.sector for m in await composite.load_universe(client, (SP500,))}
    spx = await fetch_bars("^GSPC", client, range_="20y")        # the trend filter and calendar
    # Scored against the TOTAL-RETURN index: the stocks' bars include dividends (~2 pts/yr).
    total = await fetch_bars("^SP500TR", client, range_="20y")
    bars = {}
    for i in range(0, len(symbols), CHUNK):
        got, _ = await fetch_many(tuple(symbols[i:i + CHUNK]), client, range_="20y")
        bars.update(got)
    stocks = {}
    for s, b in bars.items():
        if len(b) < 260:
            continue
        stocks[s] = sim.Stock(s, sectors.get(s, "unknown"), b.dates, b.opens, b.highs, b.lows,
                              b.closes, indicators.compute(b), {d: i for i, d in enumerate(b.dates)})
    memo: dict[date, frozenset[str]] = {}

    def members(on: date) -> frozenset[str]:
        if on not in memo:
            memo[on] = history.members_on(on, collapse_share_classes=True)
        return memo[on]

    return (stocks, list(spx.dates), dict(zip(total.dates, total.closes)), members,
            indicators.market_uptrend(spx), len(symbols) - len(stocks))


def run(recipe: rc.Recipe, data, start: date, end: date) -> sim.Result:
    stocks, calendar, spx, members, uptrend, _ = data
    return sim.with_control(recipe, stocks, calendar, spx, members, uptrend, start=start, end=end)


def entry(entries: list, stage: str, recipe: rc.Recipe, result: sim.Result, *, idea: str = "",
          verdict: str = "") -> dict:
    e = {"n": len(entries) + 1, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
         "session": date.today().isoformat(), "stage": stage, "key": recipe.key,
         "recipe": recipe.model_dump(mode="json"), "describe": recipe.describe(), "idea": idea,
         "result": result.model_dump(mode="json"), "verdict": verdict}
    ledger.append(e)
    entries.append(e)
    return e


def prompt(entries: list, today: list[str], left: int, error: str = "") -> str:
    intro = INTRO.format(s0=SEARCH[0], s1=SEARCH[1], pts=sim.BAR_EXCESS_PTS, sh=sim.BAR_SHARPE,
                         dd=sim.BAR_MAX_DRAWDOWN, mt=sim.MIN_TRADES, menu=rc.menu_text())
    session = "\n".join(today) or "(none yet)"
    fix = f"\n\nYOUR LAST ANSWER WAS REFUSED: {error}. Send a corrected recipe." if error else ""
    return (f"{intro}\n\n{ledger.summary(entries)}\n\nThis session so far:\n{session}\n\n"
            f"Recipes left this session: {left}. Write the next recipe and, in 'idea', one or two "
            f"sentences: what market behaviour it bets on and why it should beat random picks.{fix}")


async def ask_recipe(client, entries, today, left) -> tuple[rc.Recipe, str] | None:
    error = ""
    for _ in range(2):
        try:
            answer = await chat_json(prompt(entries, today, left, error), rc.schema(), client)
        except LLMError as exc:
            say(f"  Gemma did not answer: {exc}")
            return None
        try:
            return rc.parse(answer.get("recipe")), str(answer.get("idea", ""))[:600]
        except ValueError as exc:
            error = str(exc)
            say(f"  refused: {error}")
    return None


async def nominate(client, entries, data) -> None:
    hidden = ledger.tried(entries, ledger.HIDDEN_A)
    options = {e["key"]: e for e in entries if e["stage"] == ledger.SEARCH and e["key"] not in hidden}
    if not options:
        return
    schema = {"type": "object", "properties": {
        "nominate": {"type": "string", "enum": ["none", *options]}, "why": {"type": "string"}},
        "required": ["nominate", "why"]}
    ask = (f"{ledger.summary(entries, best=15, recent=15)}\n\nThe session is over. You may nominate ONE "
           f"searched recipe (by its [key]) for the hidden years, or 'none'. It runs on 2022-23 and, if it "
           f"passes your bar, on 2024-today. A recipe runs there once, ever, so nominate only one you "
           f"believe in. Bar: beat the S&P 500 by {sim.BAR_EXCESS_PTS:g} pts/yr, Sharpe > {sim.BAR_SHARPE:g}, "
           f"drawdown <= {sim.BAR_MAX_DRAWDOWN:g}%, beat random picks, >= {sim.MIN_TRADES} trades.")
    try:
        answer = await chat_json(ask, schema, client)
    except LLMError as exc:
        say(f"no nomination: {exc}")
        return
    key, why = answer.get("nominate"), str(answer.get("why", ""))[:600]
    say(f"\nNOMINATED: {key} -- {why}")
    if key not in options:
        return
    recipe = rc.parse(options[key]["recipe"])
    a = run(recipe, data, *HIDDEN_A)
    ok, verdict = sim.passes(a)
    entry(entries, ledger.HIDDEN_A, recipe, a, idea=why, verdict=f"2022-23: {verdict}")
    say(f"  hidden 2022-23: {a.line()}\n  -> {verdict}")
    if not ok:
        return
    b = run(recipe, data, HIDDEN_B_START, date.today())
    ok, verdict = sim.passes(b)
    entry(entries, ledger.HIDDEN_B, recipe, b, idea=why,
          verdict=f"2024-today: {verdict}" + (" -- CANDIDATE PAPER BOOK, operator decides" if ok else ""))
    say(f"  hidden 2024-today: {b.line()}\n  -> {verdict}")
    if ok:
        say("  *** PASSED BOTH HIDDEN STAGES: a candidate paper book. The operator decides. ***")


async def main(tries: int, check: bool) -> int:
    started = time.monotonic()
    say(f"\n## Gemma's lab {datetime.now():%Y-%m-%d %H:%M}")
    async with httpx.AsyncClient(timeout=600) as client:
        data = await load(client)
        say(f"data: {len(data[0])} stocks with history, {data[5]} past members without usable "
            f"price data (mostly delisted -- results are flattered by their absence); "
            f"{time.monotonic() - started:.0f}s")
        if check:
            recipe = rc.parse(CHECK_RECIPE)
            t = time.monotonic()
            result = run(recipe, data, *SEARCH)
            say(f"check: {recipe.describe()}\n  {result.line()}\n  {time.monotonic() - t:.0f}s")
            return 0
        entries = ledger.read()
        today: list[str] = []
        for left in range(tries, 0, -1):
            got = await ask_recipe(client, entries, today, left)
            if got is None:
                continue
            recipe, idea = got
            seen = [e for e in entries if e["stage"] == ledger.SEARCH and e["key"] == recipe.key]
            if seen:
                today.append(f"- [{recipe.key}] ALREADY RUN as #{seen[0]['n']}: "
                             f"{seen[0]['result']['annual_pct']:+.1f}%/yr. Not run again.")
                continue
            result = run(recipe, data, *SEARCH)
            e = entry(entries, ledger.SEARCH, recipe, result, idea=idea)
            verdict = sim.passes(result)[1]
            today.append(f"- #{e['n']} [{recipe.key}] {recipe.describe()} => {result.line()}. "
                         f"Your bar: {'MET' if verdict == 'passed' else 'missed -- ' + verdict}")
            say(f"#{e['n']} {recipe.describe()}\n  idea: {idea}\n  {result.line()}")
        await nominate(client, entries, data)
    say(f"done in {(time.monotonic() - started) / 60:.0f} min; log {ledger.LOG}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tries", type=int, default=TRIES)
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.tries, a.check)))
