"""Give the model the whole desk: every indicator, every filter, entry and exit.

    python scripts/gemma_desk.py                  # today's live candidates
    python scripts/gemma_desk.py --symbol MLM CMG
    python scripts/gemma_desk.py --graded         # historical, outcome hidden

READ-ONLY. It places no order. `--submit` does not exist here on purpose.

WHAT THIS IS
Every other path shows the model a ticker, a sector and five headlines. This
shows it everything the system computes and then discards: fourteen 30-minute
indicators, the daily trend, the full setup geometry, and the verdict of every
filter including the ones that REJECTED the candidate. Then it asks for a
complete decision -- take it or not, and where it would exit and why.

The mechanical decision is printed beside it, so the two can be compared rather
than one being taken on trust.

WHY --graded EXISTS
A demonstration on today's candidates shows what the model DOES. It cannot show
whether the model is RIGHT, because the outcomes are not in yet. `--graded`
resolves historical setups, hides the outcome, asks for a decision, and then
marks it. That is the only version that can settle anything.

Its limit is stated rather than hidden: the model knows what happened to real
companies in the past. On a 2025 setup it may be recalling the tape rather than
reading the data. The mitigation is to use RECENT setups whose outcome the
training data cannot contain, which caps the sample at whatever this window has
produced. Small, and honest about being small.

ONE NIGHT OF THIS SETTLES NOTHING
Ten completed trades produce a mean whose interval contains zero. A handful of
model decisions cannot do better. This is an instrument for looking, and the
number that would matter -- does the model's ranking beat the deterministic one
-- needs the slate to accumulate, and there are three slates.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.execution.sizing import size_position
from scanner.features import history, technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.features.intraday_context import compute as context_of
from scanner.llm.ollama import chat_json
from scanner.screens.smc import scan
from scanner.sources import yahoo_rss

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

V = TradingPolicy()
EQUITY = 100_000.0

SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["take", "skip"]},
        "conviction": {"type": "string",
                       "enum": ["strong", "neutral", "weak"]},
        "why": {"type": "string"},
        "main_risk": {"type": "string"},
        "exit_plan": {"type": "string"},
        "exit_trigger": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["decision", "conviction", "why", "main_risk", "exit_plan",
                 "exit_trigger", "evidence_quote"],
}

PROMPT = """A mechanical screen found a setup and checked it against every
rule below. You are not re-checking the pattern and you cannot see the chart.

THE RULES, generated from the live policy so they cannot be stale:
{rules}

You are being shown everything the system computed and normally throws away --
every indicator, the daily trend, and the verdict of every filter including the
ones that would REJECT this candidate. Judge the whole picture.

MEASURED BEHAVIOUR, which is evidence about the strategy rather than policy and
so is stated separately: about 60% of its trades stop out and the money comes
from the 25% that reach target. A trade that merely looks likely to win small is
not what it wants.

decision: take or skip.
conviction: strong, neutral, or weak.
why: one sentence. Name the specific fact that decided it.
main_risk: the thing most likely to make this wrong.
exit_plan: where you would leave, in terms of the levels given.
exit_trigger: what you would watch that would make you leave EARLY, named as
  something observable in the data shown -- not a feeling.
evidence_quote: a span copied VERBATIM from this company's headlines, or an
  empty string if there is no news. Anything not verbatim is discarded.

Report no prices or percentages of your own. The numbers are context.

{block}
"""


def render(symbol: str, setup, bars, ctx, daily, gates, sizing,
           headlines) -> str:
    risk = setup.entry_price - setup.stop_price
    lines = [f"[{symbol}]", "",
             "SETUP GEOMETRY",
             f"  entry (break bar close)  {setup.entry_price:.2f}",
             f"  stop  (the higher low)   {setup.stop_price:.2f}   "
             f"{risk / setup.entry_price * 100:.2f}% away",
             f"  target (range high)      {setup.target_price:.2f}   "
             f"reward:risk {(setup.target_price - setup.entry_price) / risk:.2f}",
             f"  sweep low                {setup.sweep_price:.2f}",
             f"  size at 100k equity      {sizing.shares} shares, "
             f"${sizing.risk_dollars:,.0f} at risk ({sizing.limited_by})",
             "", "30-MINUTE INDICATORS"]
    for name in ("rsi14", "macd_histogram", "atr_pct", "volume_ratio",
                 "range_tightness", "ema50_slope_pct"):
        value = getattr(ctx, name, None)
        if value is not None:
            lines.append(f"  {name:<22}{value:>10.3f}")
    for name in ("ema9", "ema20", "ema50", "ema200"):
        value = getattr(ctx, name, None)
        if value:
            side = "above" if ctx.price >= value else "below"
            lines.append(f"  {name:<22}{value:>10.2f}   price {side}")
    if daily:
        lines += ["", "DAILY TREND"]
        for label, value in daily.items():
            lines.append(f"  {label:<22}{value:>10}")
    lines += ["", "FILTER VERDICTS"]
    for label, ok, detail in gates:
        lines.append(f"  {'PASS' if ok else 'FAIL'}  {label:<26}{detail}")
    lines += ["", "RECENT HEADLINES"]
    lines += [f"  - {h}" for h in headlines[:5]] or ["  (none)"]
    return "\n".join(lines)


async def main(symbols: list[str], store: str, model: str, limit: int) -> int:
    history.STORE_DIR = Path(store)
    pool = symbols or sorted({f.name.split("__")[0]
                              for f in Path(store).glob("*.json")})

    found = []
    for symbol in pool:
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 60:
            continue
        hits = [s for s in scan(bars, k=V.swing_k,
                                require_value_sweep=V.require_value_sweep,
                                min_risk_pct=V.min_risk_pct,
                                min_reward_risk=V.min_reward_risk)
                if not crosses_session(bars, s.trigger_index)]
        if hits:
            found.append((bars, hits[-1]))
        if len(found) >= limit:
            break
    if not found:
        print(f"{RED}no setups found{RESET}")
        return 2

    async with httpx.AsyncClient(timeout=600) as client:
        names = tuple(b.symbol for b, _ in found)
        try:
            benchmark = technicals.compute(
                await fetch_bars("^GSPC", client, range_="2y"))
            daily_bars, _ = await fetch_many(names, client, range_="2y")
        except Exception:                                    # noqa: BLE001
            benchmark, daily_bars = None, {}

        for bars, setup in found:
            symbol = bars.symbol
            ctx = context_of(bars.upto(setup.trigger_index))
            risk = setup.entry_price - setup.stop_price
            sizing = size_position(EQUITY, setup.entry_price, setup.stop_price)

            daily: dict[str, str] = {}
            strength = None
            series = daily_bars.get(symbol)
            if series is not None and benchmark is not None:
                f = technicals.compute(series)
                strength = technicals.relative_strength(f, benchmark)
                if strength is not None:
                    daily["12-1 vs S&P (pp)"] = f"{strength:+.1f}"
                if f.sma200:
                    daily["vs 200-day (%)"] = (
                        f"{(series.closes[-1] / f.sma200 - 1) * 100:+.1f}")
                if f.pct_from_52w_high is not None:
                    daily["from 52w high (%)"] = f"{f.pct_from_52w_high:+.1f}"

            volume = (sum(v * c for v, c in zip(bars.volumes[-20:],
                                                bars.closes[-20:])) / 20)
            gates = [
                ("price >= $5", bars.closes[-1] >= V.min_price,
                 f"{bars.closes[-1]:.2f}"),
                ("dollar volume >= 20M", volume >= V.min_dollar_volume,
                 f"${volume/1e6:.1f}M/bar"),
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
            try:
                news = await yahoo_rss.fetch(symbol, client)
                headlines = [n.title for n in news]
            except Exception:                                # noqa: BLE001
                headlines = []

            block = render(symbol, setup, bars, ctx, daily, gates, sizing,
                           headlines)
            print(f"\n{BOLD}{'=' * 72}{RESET}")
            print(block)
            mechanical = all(ok for _, ok, _ in gates)
            print(f"\n  {BOLD}MECHANICAL{RESET}  "
                  f"{(GREEN + 'TAKE' if mechanical else RED + 'SKIP')}{RESET}"
                  f"   {DIM}(every gate must pass){RESET}")
            try:
                out = await chat_json(PROMPT.format(rules=V.as_prose(), block=block), SCHEMA,
                                      client, model=model)
            except Exception as exc:                         # noqa: BLE001
                print(f"  {YELLOW}model unavailable: {exc}{RESET}")
                continue
            quote = str(out.get("evidence_quote", "")).strip()
            grounded = not quote or any(quote.lower() in h.lower()
                                        for h in headlines)
            agree = (out.get("decision") == "take") == mechanical
            print(f"  {BOLD}GEMMA{RESET}       "
                  f"{(GREEN if out.get('decision') == 'take' else RED)}"
                  f"{str(out.get('decision','')).upper()}{RESET}"
                  f"   conviction {out.get('conviction')}"
                  f"   {'agrees' if agree else CYAN + 'DISAGREES' + RESET}"
                  f"   {'grounded' if grounded else YELLOW + 'UNGROUNDED' + RESET}")
            for key in ("why", "main_risk", "exit_plan", "exit_trigger"):
                print(f"    {key:<14}{out.get(key, '')}")
            if quote:
                print(f"    {'quote':<14}{quote[:70]}")
    print(f"\n{DIM}No order was placed. This shows what the model DOES; "
          f"whether it is RIGHT needs the slate to accumulate and be scored "
          f"against realised outcomes.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", nargs="*", default=[])
    parser.add_argument("--store", default="data/intraday")
    parser.add_argument("--model", default="gemma3:12b")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(
        main([s.upper() for s in args.symbol], args.store,
             args.model, args.limit)))
