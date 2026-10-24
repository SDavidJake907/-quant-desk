"""Gemma's research lab (operator 2026-10-23: "let her run backtests, maybe she will find
a strategy that works"; then "let her backtest all weekend. big caps, med caps, small caps").
Rules in scanner/lab/.

    python scripts/gemma_lab.py --universe mid      # one session: 15 recipes + one nomination
    python scripts/gemma_lab.py --weekend           # sessions all weekend, rotating big/mid/small
    python scripts/gemma_lab.py --check --universe small   # load the data, run one fixed recipe

She writes a recipe from the menu, it runs as a whole book on the SEARCH years
with the random control beside it, she sees the result and writes the next.
She may nominate ONE searched recipe for the hidden years: stage A (2025)
first, stage B (2026 to today) only if A passes her bar. In --weekend mode only
each universe's LAST session nominates: ~300 searches must not turn into ~20
shots at the hidden years, or they stop being hidden. A recipe that passes both
is reported to the operator as a candidate paper book. Nothing here trades.
Every run is logged in data/lab/log.jsonl and counts as a trial.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from array import array
from datetime import date, datetime, timedelta

import httpx
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.features.bars import fetch_bars, fetch_many
from scanner.lab import baselines, entry_study, exit_study, indicators, ledger, recipe as rc, rotation_study, sim
from scanner.llm.ollama import LLMError, chat_json
from scanner.models import Frozen
from scanner.sources import composite, index_history
from scanner.sources.index_history import IndexSpec, SP400, SP500, SP600


#: Operator 2026-10-23 ("lets use the most recent data also"): the search runs to the end of
#: 2024 (the 2022 bear market and the 2023-24 recovery included); the newest ~22 months stay
#: hidden as Gemma's two-stage test (2025, then 2026 to today). Searching to today would leave
#: no unseen years -- only live paper trading could test a winner then.
SEARCH_END = date(2024, 12, 31)
A, B = "2025", "2026 to today"


class Universe(Frozen):
    key: str
    label: str
    spec: IndexSpec
    search: tuple[date, date]
    #: Total-return benchmark (an ETF's adjusted closes, or the S&P 500 TR index).
    benchmark: str
    caveat: str = ""


#: Membership history (measured 2026-10-23): S&P 500 change log from 1976, MidCap 400
#: from 2012-01, SmallCap 600 only from 2019-12 -- before that its "members" are today's
#: survivors walked back, which flatters every result. The hidden years are clean for all.
UNIVERSES = {
    "large": Universe(key="large", label="big caps (S&P 500)", spec=SP500,
                      search=(date(2007, 7, 1), SEARCH_END), benchmark="^SP500TR"),
    "mid": Universe(key="mid", label="mid caps (S&P MidCap 400)", spec=SP400,
                    search=(date(2012, 7, 1), SEARCH_END), benchmark="MDY"),
    "small": Universe(key="small", label="small caps (S&P SmallCap 600)", spec=SP600,
                      search=(date(2012, 7, 1), SEARCH_END), benchmark="IJR",
                      caveat="Membership before 2020 is today's survivors walked back (stocks that "
                             "collapsed out are missing), so absolute returns are flattered: beating "
                             "RANDOM picks is the comparison that counts here."),
}
HIDDEN_A = (date(2025, 1, 1), date(2025, 12, 31))
HIDDEN_B_START = date(2026, 1, 1)
TRIES = 15                     # Gemma asked for 15 (2026-10-23)
#: Operator 2026-10-23: "find the best entry and exit, holding best 3-4 weeks".
FOCUS_HOLD = (15, 21)
CHUNK = 100
#: --weekend: a session starts every SESSION_EVERY, rotating the universes, until
#: Sunday WEEKEND_END (local time) -- leaving Ollama free before her Sunday decision.
SESSION_EVERY = timedelta(minutes=30)        # operator: "all weekend" -- a session takes ~2-10 min
WEEKEND_END_HOUR = 14

CHECK_RECIPE = {"filters": [{"name": "price_vs_sma200_pct", "op": "above", "value": 0},
                            {"name": "market_uptrend", "op": "is", "value": 1}],
                "rank_by": "mom_12_1_pct", "rank_order": "high_first", "top_n": 10,
                "rebalance_days": 21, "stop_atr": 0.0, "target_r": 0.0, "max_hold_days": 63,
                "max_per_sector": 3}

INTRO = """You are in your RESEARCH LAB (weekend; markets closed). You design swing-trading strategies
as recipes; the system backtests each one as a whole book and shows you the result. Nothing here trades.

THIS SESSION'S STOCKS: {label}, members as of each date (companies that later left are included where
price data exists). {caveat}
How a recipe is run: every `rebalance_days` trading days, on the close, the empty slots of a `top_n`-slot
book are filled with the best-ranked members passing ALL filters; the buy is at the NEXT day's close;
each slot is 1/top_n of the book. Exits from the next day: stop and target (a day that opens through
a level fills at the open; a day touching both is a stop), then the time limit at the close. Costs are
included. Each result is shown beside RANDOM picks from the same stocks run through the same book and
exits, and beside the index ({bench}, dividends included). If you cannot beat random picks, the idea is
not adding anything. IDLE CASH (empty slots) sits in the index and earns its return, so every recipe
starts at the index: the question is whether your picks ADD to it. "invested" is the share held in stocks.

Search years: {s0} to {s1}. The years after are HIDDEN from you. {nominate}Your bar on the hidden
years: beat the index by {pts:g} points a year, a Sharpe above the index's, a worst drawdown no more than {dd:g}
points deeper than the index's -- and
beat random picks with at least {mt} trades. With enough tries one recipe looks good by luck; every
try is counted, so make each one a real idea, not a small tweak of a lucky number. Explore DIFFERENT
families across a session -- trend following, buying dips, breakouts, volume, volatility squeezes,
calendar (weekday, month), overnight vs regular-session moves -- instead of re-tuning one family.

THIS WEEKEND'S FOCUS (operator, 2026-10-23): find the best ENTRY and the best EXIT for a book that
holds each stock 3 to 4 weeks (max_hold_days 15 or 21) and then replaces it with the strongest names
at that moment -- the same stock again if it still ranks. Use the sell rules, the trail, stops and
targets to get out EARLIER when a trade fails; the 3-4 week limit sells the rest. What the desk's
studies already found on the search years (in your log as baselines): mid caps are where entries
work (momentum 12-1 rotated every 2-4 weeks passed your bar in 7 of 8 versions); big caps beat
nothing; 10 names did better than 20; a 2.5-ATR safety stop on a 3-week hold usually hurt; quick
indicator sells (close under the 20/50-day, MACD, SuperTrend) cut winners. New on the menu:
rrs_5d / rrs_20d, Real Relative Strength (strength vs the S&P 500 beyond the stock's own volatility).

THE MENU
{menu}"""


def say(text: str) -> None:
    print(text, flush=True)


def _compact(series) -> array:
    """8 bytes a value instead of ~32 (a float object plus a list slot); None -> NaN."""
    return array("d", (math.nan if v is None else float(v) for v in series))


async def load(client, u: Universe) -> tuple:
    """(stocks, calendar, benchmark closes, members, uptrend, missing count)."""
    history = await index_history.load(client, spec=u.spec)
    start = u.search[0] - timedelta(days=400)                    # a year of warm-up for 12-1 momentum
    symbols = sorted(history.symbols_ever(start, date.today(), collapse_share_classes=True))
    sectors = {m.symbol: m.sector for m in await composite.load_universe(client, (u.spec,))}
    spx = await fetch_bars("^GSPC", client, range_="20y")        # the trend filter and calendar
    bench = await fetch_bars(u.benchmark, client, range_="20y")
    market = dict(zip(spx.dates, spx.closes))                    # relative strength vs the S&P 500
    stocks = {}
    for i in range(0, len(symbols), CHUNK):
        got, _ = await fetch_many(tuple(symbols[i:i + CHUNK]), client, range_="20y")
        for s, b in got.items():
            if len(b) < 260:
                continue
            stocks[s] = sim.Stock(s, sectors.get(s, "unknown"), b.dates, _compact(b.opens),
                                  _compact(b.highs), _compact(b.lows), _compact(b.closes),
                                  {k: _compact(v) for k, v in
                                   {**indicators.compute(b), **indicators.more(b, market, spx)}.items()},
                                  {d: j for j, d in enumerate(b.dates)})
    memo: dict[date, frozenset[str]] = {}

    def members(on: date) -> frozenset[str]:
        if on not in memo:
            memo[on] = history.members_on(on, collapse_share_classes=True)
        return memo[on]

    return (stocks, list(spx.dates), dict(zip(bench.dates, bench.closes)), members,
            indicators.market_uptrend(spx), len(symbols) - len(stocks))


def run(recipe: rc.Recipe, data, start: date, end: date) -> sim.Result:
    stocks, calendar, bench, members, uptrend, _ = data
    return sim.with_control(recipe, stocks, calendar, bench, members, uptrend, start=start, end=end)


def entry(entries: list, u: Universe, stage: str, recipe: rc.Recipe, result: sim.Result, *,
          idea: str = "", verdict: str = "", name: str = "") -> dict:
    e = {"n": len(entries) + 1, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
         "session": date.today().isoformat(), "universe": u.key, "stage": stage, "key": recipe.key,
         "scoring": sim.SCORING,
         "recipe": recipe.model_dump(mode="json"), "describe": recipe.describe(), "idea": idea,
         "result": result.model_dump(mode="json"), "verdict": verdict, **({"name": name} if name else {})}
    ledger.append(e)
    entries.append(e)
    return e


def prompt(u: Universe, entries: list, today: list[str], left: int, nominates: bool,
           error: str = "") -> str:
    later = (f"At the end of this session you may nominate ONE recipe for them: {A} first, then "
             f"{B} only if it passes. " if nominates else
             "This weekend you nominate ONE recipe per stock group for them, in that group's last "
             "session -- so search widely now. ")
    intro = INTRO.format(label=u.label, caveat=u.caveat, bench=u.benchmark, s0=u.search[0], s1=u.search[1],
                         nominate=later, pts=sim.BAR_EXCESS_PTS,
                         dd=sim.BAR_DD_OVER_INDEX, mt=sim.MIN_TRADES, menu=rc.menu_text())
    session = "\n".join(today) or "(none yet)"
    fix = f"\n\nYOUR LAST ANSWER WAS REFUSED: {error}. Send a corrected recipe." if error else ""
    return (f"{intro}\n\n{ledger.summary(entries, universe=u.key, scoring=sim.SCORING)}\n\nThis session so far:\n{session}\n\n"
            f"Recipes left this session: {left}. Write the next recipe and, in 'idea', one or two "
            f"sentences: what market behaviour it bets on and why it should beat random picks.{fix}")


def focus_schema() -> dict:
    """Her recipe schema with the hold limited to FOCUS_HOLD (3-4 weeks)."""
    schema = rc.schema()
    schema["properties"]["recipe"]["properties"]["max_hold_days"]["enum"] = list(FOCUS_HOLD)
    return schema


def in_focus(recipe: rc.Recipe) -> rc.Recipe:
    """A hold outside FOCUS_HOLD becomes the nearest one inside it."""
    if recipe.max_hold_days in FOCUS_HOLD:
        return recipe
    return recipe.model_copy(update={"max_hold_days": min(FOCUS_HOLD, key=lambda d: abs(d - recipe.max_hold_days))})


async def ask_recipe(client, u, entries, today, left, nominates) -> tuple[rc.Recipe, str] | None:
    error = ""
    for _ in range(2):
        try:
            answer = await chat_json(prompt(u, entries, today, left, nominates, error), focus_schema(), client)
        except LLMError as exc:
            say(f"  Gemma did not answer: {exc}")
            return None
        try:
            return in_focus(rc.parse(answer.get("recipe"))), str(answer.get("idea", ""))[:600]
        except ValueError as exc:
            error = str(exc)
            say(f"  refused: {error}")
    return None


async def nominate(client, u: Universe, entries, data) -> None:
    if ledger.nominated_since(entries, u.key, datetime.now().astimezone() - timedelta(days=3)):
        say(f"\n{u.key}: already had its hidden-years run this weekend (a restarted weekend run) -- none now")
        return
    hidden = ledger.tried(entries, ledger.HIDDEN_A, universe=u.key)
    options = {e["key"]: e for e in entries if e["stage"] == ledger.SEARCH
               and ledger.universe_of(e) == u.key and e["key"] not in hidden
               and ledger.scoring_of(e) == sim.SCORING}
    if not options:
        return
    schema = {"type": "object", "properties": {
        "nominate": {"type": "string", "enum": ["none", *options]}, "why": {"type": "string"}},
        "required": ["nominate", "why"]}
    ask = (f"{ledger.summary(entries, universe=u.key, best=15, recent=15, scoring=sim.SCORING)}\n\nYou may now nominate ONE "
           f"searched recipe for {u.label} (by its [key]) for the hidden years, or 'none'. It runs on "
           f"{A} and, if it passes your bar, on {B}. A recipe runs there once, ever, so nominate "
           f"only one you believe in. Bar: beat {u.benchmark} by {sim.BAR_EXCESS_PTS:g} pts/yr, a Sharpe above "
           f"the index's, drawdown no more than {sim.BAR_DD_OVER_INDEX:g} pts deeper than the index's, beat random picks, >= {sim.MIN_TRADES} "
           "trades.")
    try:
        answer = await chat_json(ask, schema, client)
    except LLMError as exc:
        say(f"no nomination: {exc}")
        return
    key, why = answer.get("nominate"), str(answer.get("why", ""))[:600]
    say(f"\nNOMINATED ({u.key}): {key} -- {why}")
    if key not in options:
        return
    recipe = rc.parse(options[key]["recipe"])
    a = run(recipe, data, *HIDDEN_A)
    ok, verdict = sim.passes(a)
    entry(entries, u, ledger.HIDDEN_A, recipe, a, idea=why, verdict=f"{A}: {verdict}")
    say(f"  hidden {A}: {a.line()}\n  -> {verdict}")
    if not ok:
        return
    b = run(recipe, data, HIDDEN_B_START, date.today())
    ok, verdict = sim.passes(b)
    entry(entries, u, ledger.HIDDEN_B, recipe, b, idea=why,
          verdict=f"{B}: {verdict}" + (" -- CANDIDATE PAPER BOOK, operator decides" if ok else ""))
    say(f"  hidden {B}: {b.line()}\n  -> {verdict}")
    if ok:
        say(f"  *** {u.key}: PASSED BOTH HIDDEN STAGES: a candidate paper book. The operator decides. ***")


async def session(client, u: Universe, tries: int, nominates: bool) -> None:
    started = time.monotonic()
    say(f"\n## Gemma's lab -- {u.label} -- {datetime.now():%a %Y-%m-%d %H:%M}")
    data = await load(client, u)
    say(f"data: {len(data[0])} stocks with history, {data[5]} past members without usable price data "
        f"(mostly delisted -- results are flattered by their absence); {time.monotonic() - started:.0f}s")
    entries = ledger.read()
    today: list[str] = []
    for left in range(tries, 0, -1):
        got = await ask_recipe(client, u, entries, today, left, nominates)
        if got is None:
            continue
        recipe, idea = got
        seen = [e for e in entries if e["stage"] == ledger.SEARCH and e["key"] == recipe.key
                and ledger.universe_of(e) == u.key and ledger.scoring_of(e) == sim.SCORING]
        if seen:
            today.append(f"- [{recipe.key}] ALREADY RUN as #{seen[0]['n']}: "
                         f"{seen[0]['result']['annual_pct']:+.1f}%/yr. Not run again.")
            continue
        result = run(recipe, data, *u.search)
        e = entry(entries, u, ledger.SEARCH, recipe, result, idea=idea)
        verdict = sim.passes(result)[1]
        today.append(f"- #{e['n']} [{recipe.key}] {recipe.describe()} => {result.line()}. "
                     f"Your bar: {'MET' if verdict == 'passed' else 'missed -- ' + verdict}")
        say(f"#{e['n']} {recipe.describe()}\n  idea: {idea}\n  {result.line()}")
    if nominates:
        await nominate(client, u, entries, data)
    say(f"session done in {(time.monotonic() - started) / 60:.0f} min; log {ledger.LOG}")


def weekend_plan(now: datetime, order: tuple[str, ...] = ("large", "mid", "small")) -> list[tuple[datetime, str, bool]]:
    """(start, universe, nominates) from now until Sunday WEEKEND_END_HOUR; each universe's
    last session nominates."""
    days_to_sunday = (6 - now.weekday()) % 7
    end = (now + timedelta(days=days_to_sunday)).replace(hour=WEEKEND_END_HOUR, minute=0, second=0,
                                                         microsecond=0)
    plan, t, i = [], now, 0
    while t < end:
        plan.append((t, order[i % len(order)]))
        t += SESSION_EVERY
        i += 1
    last = {u: max(j for j, (_, x) in enumerate(plan) if x == u) for u in {x for _, x in plan}}
    return [(t, u, last[u] == j) for j, (t, u) in enumerate(plan)]


async def run_baselines(client) -> None:
    """The classic-strategy library (lab/baselines.py) on every stock group, search years only,
    each once per scoring version; shown to Gemma as reference rows and counted as trials."""
    entries = ledger.read()
    for u in UNIVERSES.values():
        done = {(e.get("name"), e["key"]) for e in entries if e.get("stage") == ledger.BASELINE
                and ledger.universe_of(e) == u.key and ledger.scoring_of(e) == sim.SCORING}
        todo = [(n, rc.parse(r)) for n, r in baselines.BASELINES]
        todo = [(n, r) for n, r in todo if (n, r.key) not in done]
        if not todo:
            continue
        say(f"\n## Desk baselines -- {u.label}")
        data = await load(client, u)
        for name, recipe in todo:
            result = run(recipe, data, *u.search)
            entry(entries, u, ledger.BASELINE, recipe, result, name=name, verdict=sim.passes(result)[1])
            say(f"- {name}: {result.line()}")


EXIT_REPORT = "logs/lab/exit_study.md"


async def run_exit_study(client) -> None:
    """Where to sell (lab/exit_study.py): every exit x both entries x every stock group,
    search years only; counted (stage exit_study); report in EXIT_REPORT."""
    entries = ledger.read()
    cells: dict[tuple[str, str], dict[str, sim.Result]] = {}
    for u in UNIVERSES.values():
        say(f"\n## Exit study -- {u.label}")
        data = await load(client, u)
        for en, xn, raw in exit_study.grid():
            recipe = rc.parse(raw)
            result = run(recipe, data, *u.search)
            entry(entries, u, "exit_study", recipe, result, name=f"{en} / {xn}", verdict=sim.passes(result)[1])
            cells.setdefault((u.key, en), {})[xn] = result
            say(f"- {en} / {xn}: {result.line()}")
    lines = ["# Exit study (search years; idle cash in the index; costs; random control)", "",
             "Points a year vs the group's index, per exit. 'vs hold 3m' = the same entry held 3 months.", ""]
    names = [x for x, _ in exit_study.EXITS]
    score: dict[str, list[float]] = {x: [] for x in names}
    for (uk, en), by_exit in cells.items():
        base = by_exit["hold 3 months"]
        lines += [f"## {UNIVERSES[uk].label} -- {en}", "",
                  "| exit | vs index | vs random | vs hold 3m | Sharpe (index) | worst DD (index) | trades | bar |",
                  "|---|---|---|---|---|---|---|---|"]
        for x in sorted(names, key=lambda x: -by_exit[x].excess_pts):
            r = by_exit[x]
            rnd = r.annual_pct - (r.random_annual_pct or 0.0)
            score[x].append(r.annual_pct - base.annual_pct)
            lines.append(f"| {x} | {r.excess_pts:+.1f} | {rnd:+.1f} | {r.annual_pct - base.annual_pct:+.1f} | "
                         f"{r.sharpe:.2f} ({r.index_sharpe:.2f}) | {r.max_drawdown_pct:.0f}% ({r.index_drawdown_pct:.0f}%) | "
                         f"{r.trades} | {'MET' if sim.passes(r)[0] else '-'} |")
        lines.append("")
    lines += ["## Across all six cells", "", "| exit | average vs hold 3m | cells better than hold 3m |", "|---|---|---|"]
    for x in sorted(names, key=lambda x: -sum(score[x]) / max(1, len(score[x]))):
        v = score[x]
        lines.append(f"| {x} | {sum(v) / max(1, len(v)):+.1f} pts/yr | {sum(d > 0 for d in v)} of {len(v)} |")
    Path(EXIT_REPORT).parent.mkdir(parents=True, exist_ok=True)
    Path(EXIT_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"\nreport: {EXIT_REPORT}")


ENTRY_REPORT = "logs/lab/entry_study.md"


async def run_entry_study(client) -> None:
    """The best entry with the best exits, per gate (lab/entry_study.py). Needs the exit study."""
    rows = [e for e in ledger.read() if e.get("stage") == "exit_study" and ledger.scoring_of(e) == sim.SCORING]
    exits = list(dict.fromkeys([*entry_study.best_exits(rows), entry_study.LIVE_EXIT]))
    if len(exits) < 2:
        say("run --exit-study first: the entry study uses its best exits")
        return
    say(f"exits under test: {exits}")
    entries = ledger.read()
    table: dict[str, dict[tuple[str, str], sim.Result]] = {}
    for u in UNIVERSES.values():
        say(f"\n## Entry study -- {u.label}")
        data = await load(client, u)
        for en, xn, raw in entry_study.grid(exits):
            recipe = rc.parse(raw)
            result = run(recipe, data, *u.search)
            entry(entries, u, "entry_study", recipe, result, name=f"{en} / {xn}", verdict=sim.passes(result)[1])
            table.setdefault(u.key, {})[(en, xn)] = result
            say(f"- {en} / {xn}: {result.line()}")
    gate = {"large": "gate 1, big caps", "mid": "gate 2, mid caps", "small": "gate 3, small caps"}
    lines = ["# Entry study: the best entry with the best exits, per gate", "",
             f"Exits: {', '.join(exits)}. Points a year vs the group's index (vs random in brackets); "
             "MET = passes Gemma's bar. Search years; idle cash in the index; costs.", ""]
    for uk, cells in table.items():
        lines += [f"## {gate[uk]} ({UNIVERSES[uk].label})", "",
                  "| entry | " + " | ".join(exits) + " | average |", "|---|" + "---|" * (len(exits) + 1)]
        names = [en for en, _ in entry_study.ENTRIES]
        avg = {en: sum(cells[(en, x)].excess_pts for x in exits) / len(exits) for en in names}
        for en in sorted(names, key=lambda n: -avg[n]):
            row = []
            for x in exits:
                r = cells[(en, x)]
                rnd = r.annual_pct - (r.random_annual_pct or 0.0)
                row.append(f"{r.excess_pts:+.1f} ({rnd:+.1f}){' MET' if sim.passes(r)[0] else ''}")
            lines.append(f"| {en} | " + " | ".join(row) + f" | {avg[en]:+.1f} |")
        best = max(cells, key=lambda k: cells[k].excess_pts)
        r = cells[best]
        lines += ["", f"Best pair for {gate[uk]}: **{best[0]} + {best[1]}** -- {r.line()}", ""]
    Path(ENTRY_REPORT).parent.mkdir(parents=True, exist_ok=True)
    Path(ENTRY_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"\nreport: {ENTRY_REPORT}")


MOMENTUM_REPORT = "logs/lab/momentum_study.md"
#: Operator 2026-10-23: "lets try different momentum.. 12-2 12-3 12-4 ... 11-1 11-2 11-3. all
#: the way down". Return from L months ago to S months ago (21 trading days a month).
MOMENTUM_WINDOWS = tuple((lb, sk) for lb in range(12, 0, -1) for sk in range(0, lb))


def momentum_series(closes, lb: int, sk: int) -> array:
    n, a, b = len(closes), 21 * lb, 21 * sk
    return array("d", (closes[i - b] / closes[i - a] - 1 if i >= a and closes[i - a] else math.nan
                       for i in range(n)))


async def run_momentum_study(client) -> None:
    """Every momentum window L-S on every gate, the same 3-week rotation book (top 10, refilled
    weekly, no filters), so only the window changes. Counted (stage momentum_study)."""
    entries = ledger.read()
    grid: dict[str, dict[tuple[int, int], sim.Result]] = {}
    for u in UNIVERSES.values():
        say(f"\n## Momentum study -- {u.label}")
        data = await load(client, u)
        stocks, calendar, bench, members, uptrend, _ = data
        control = None
        for lb, sk in MOMENTUM_WINDOWS:
            for st in stocks.values():
                st.ind["mom_window"] = momentum_series(st.closes, lb, sk)
            recipe = rc.Recipe(filters=(), rank_by="mom_window", rank_order="high_first", top_n=10,
                               rebalance_days=5, stop_atr=0.0, target_r=0.0, max_hold_days=15)
            if control is None:
                result = sim.with_control(recipe, stocks, calendar, bench, members, uptrend, start=u.search[0],
                                          end=u.search[1])
                control = result.random_annual_pct
            else:
                result = sim.simulate(recipe, stocks, calendar, bench, members, uptrend, start=u.search[0],
                                      end=u.search[1]).model_copy(update={"random_annual_pct": control})
            entry(entries, u, "momentum_study", recipe, result, name=f"momentum {lb}-{sk}",
                  verdict=sim.passes(result)[1])
            grid.setdefault(u.key, {})[(lb, sk)] = result
            say(f"- momentum {lb}-{sk}: {result.line()}")
    gate = {"large": "gate 1, big caps", "mid": "gate 2, mid caps", "small": "gate 3, small caps"}
    lines = ["# Momentum windows L-S (return from L months ago to S months ago)", "",
             "Book: top 10 by the window, held 3 weeks, refilled weekly, no filters; search years; idle cash in "
             "the index; costs. Cells: points a year vs the group's index; * = passes Gemma's bar. A real effect "
             "is a smooth REGION of good cells; one bright cell among dull neighbours is luck.", ""]
    for uk, cells in grid.items():
        lines += [f"## {gate[uk]}", "", "| L \\ S | " + " | ".join(str(s) for s in range(12)) + " |",
                  "|---|" + "---|" * 12]
        for lb in range(12, 0, -1):
            row = []
            for sk in range(12):
                r = cells.get((lb, sk))
                row.append("" if r is None else f"{r.excess_pts:+.1f}{'*' if sim.passes(r)[0] else ''}")
            lines.append(f"| {lb} | " + " | ".join(row) + " |")
        best = max(cells, key=lambda k: cells[k].excess_pts)
        lines += ["", f"Best: momentum {best[0]}-{best[1]}: {cells[best].line()}",
                  f"Classic 12-1: {cells[(12, 1)].line()}", ""]
    Path(MOMENTUM_REPORT).parent.mkdir(parents=True, exist_ok=True)
    Path(MOMENTUM_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"\nreport: {MOMENTUM_REPORT}")


ROTATION_REPORT = "logs/lab/rotation_study.md"


async def run_rotation_study(client) -> None:
    """Hold ~3 weeks then rotate (lab/rotation_study.py), per gate; report in ROTATION_REPORT."""
    entries = ledger.read()
    table: dict[str, dict[tuple[str, str], sim.Result]] = {}
    for u in UNIVERSES.values():
        say(f"\n## Rotation study -- {u.label}")
        data = await load(client, u)
        for en, shape, raw in rotation_study.grid():
            recipe = rc.parse(raw)
            result = run(recipe, data, *u.search)
            entry(entries, u, "rotation_study", recipe, result, name=f"{en} / {shape}",
                  verdict=sim.passes(result)[1])
            table.setdefault(u.key, {})[(en, shape)] = result
            say(f"- {en} / {shape}: {result.line()}")
    gate = {"large": "gate 1, big caps", "mid": "gate 2, mid caps", "small": "gate 3, small caps"}
    shapes = [s for s, *_ in rotation_study.SHAPES]
    lines = ["# Rotation study: hold 2-4 weeks, then replace (search years; idle cash in the index; costs)", "",
             "Points a year vs the group's index (vs random in brackets); MET = passes Gemma's bar.", ""]
    for uk, cells in table.items():
        lines += [f"## {gate[uk]}", "", "| entry | " + " | ".join(shapes) + " |", "|---|" + "---|" * len(shapes)]
        for en in rotation_study.ROTATION_ENTRIES:
            row = []
            for sh in shapes:
                r = cells[(en, sh)]
                rnd = r.annual_pct - (r.random_annual_pct or 0.0)
                row.append(f"{r.excess_pts:+.1f} ({rnd:+.1f}){' MET' if sim.passes(r)[0] else ''}")
            lines.append(f"| {en} | " + " | ".join(row) + " |")
        avg = {sh: sum(cells[(en, sh)].excess_pts for en in rotation_study.ROTATION_ENTRIES)
               / len(rotation_study.ROTATION_ENTRIES) for sh in shapes}
        lines.append("| **average** | " + " | ".join(f"{avg[sh]:+.1f}" for sh in shapes) + " |")
        best = max(cells, key=lambda k: cells[k].excess_pts)
        lines += ["", f"Best for {gate[uk]}: **{best[0]} -- {best[1]}**: {cells[best].line()}", ""]
    Path(ROTATION_REPORT).parent.mkdir(parents=True, exist_ok=True)
    Path(ROTATION_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"\nreport: {ROTATION_REPORT}")


async def main(universe: str, tries: int, check: bool, weekend: bool, baseline: bool = False,
               exits: bool = False, entries_study: bool = False, rotation: bool = False,
               momentum: bool = False) -> int:
    async with httpx.AsyncClient(timeout=600) as client:
        if momentum:
            await run_momentum_study(client)
            return 0
        if rotation:
            await run_rotation_study(client)
            return 0
        if exits:
            await run_exit_study(client)
            return 0
        if entries_study:
            await run_entry_study(client)
            return 0
        if baseline:
            await run_baselines(client)
            return 0
        if check:
            u = UNIVERSES[universe]
            data = await load(client, u)
            recipe = rc.parse(CHECK_RECIPE)
            say(f"check {u.key}: {len(data[0])} stocks, {data[5]} missing; {recipe.describe()}\n  "
                f"{run(recipe, data, *u.search).line()}")
            return 0
        if not weekend:
            await session(client, UNIVERSES[universe], tries, nominates=True)
            return 0
        await run_baselines(client)                      # once per scoring version; quick
        plan = weekend_plan(datetime.now().astimezone())
        say(f"weekend plan: {len(plan)} sessions until {plan[-1][0]:%a %H:%M} + one: "
            + ", ".join(f"{t:%a %H:%M} {u}{'*' if n else ''}" for t, u, n in plan) + "  (* nominates)")
        for start, key, nominates in plan:
            wait = (start - datetime.now().astimezone()).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await session(client, UNIVERSES[key], tries, nominates)
            except Exception as exc:                      # noqa: BLE001 -- one bad session must not end the weekend
                say(f"SESSION FAILED ({key}): {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--universe", choices=sorted(UNIVERSES), default="large")
    p.add_argument("--tries", type=int, default=TRIES)
    p.add_argument("--check", action="store_true")
    p.add_argument("--weekend", action="store_true")
    p.add_argument("--baseline", action="store_true", help="run the classic-strategy library on all groups")
    p.add_argument("--exit-study", action="store_true", help="where to sell: every exit x entry x group")
    p.add_argument("--entry-study", action="store_true", help="the best entry with the exit study's best exits, per gate")
    p.add_argument("--rotation-study", action="store_true", help="hold 2-4 weeks then replace, per gate")
    p.add_argument("--momentum-study", action="store_true", help="every momentum window L-S, per gate")
    a = p.parse_args()
    sys.exit(asyncio.run(main(a.universe, a.tries, a.check, a.weekend, a.baseline, a.exit_study,
                              a.entry_study, a.rotation_study, a.momentum_study)))
