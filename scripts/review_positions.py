"""The model reviews every open position, every scan, and its call is recorded.

    python scripts/review_positions.py
    python scripts/review_positions.py --model qwen2.5:14b-instruct

READ-ONLY AGAINST THE BROKER. It reads positions and writes to the audit log.
There is no --execute and no close path. That is the point.

WHAT WATCHES A POSITION TODAY
Three things, all mechanical. The bracket's stop and target, which the broker
holds. `manage_positions.py`, which closes anything past the 60-bar limit.
`flatten.py`, which closes everything before the weekend.

Nothing READS the position. A name can lose its trend, have its thesis
contradicted by an overnight headline, or see its sector roll over, and the
system holds it until a price level is touched. The exits are structural, which
is right, but structure is not the only thing that changes.

WHY THIS IS RECORDED AND NOT ENFORCED
The model's judgement on individual trades has never been scored. There are
three slates. `gemma_audit.py` shows it reasons well about the SYSTEM -- 2/2,
and it identified the outlier trade and the optimistic fill assumption before
either was confirmed here -- but that is a different question from whether its
per-position calls make money.

An exit is also the most dangerous authority to hand over. A model-driven close
overrides the bracket, and the bracket is the only guaranteed protection this
system has. A wrong entry costs one R. A wrong exit can leave a position
unprotected if the close half-fails.

So this follows the pattern the policy already uses for `shadow_rsi_gate`,
`shadow_session_slot`, `shadow_weekday` and `shadow_llm_ranking`: record what
the rule WOULD have done, change nothing, and enable it later against evidence
rather than against confidence. The audit log can then be replayed to ask a
precise question -- did the positions it wanted to exit early do worse than the
ones it wanted to hold?

That question needs weeks of records, not one night. This starts the clock.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.audit import append_event
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.features import history, technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.features.intraday_context import compute as context_of
from scanner.llm.ollama import chat_json
from scanner.models import AuditEvent
from scanner.sources import yahoo_rss

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

V = TradingPolicy()

SCHEMA = {
    "type": "object",
    "properties": {
        "call": {"type": "string", "enum": ["hold", "exit_early"]},
        "confidence": {"type": "string",
                       "enum": ["strong", "neutral", "weak"]},
        "why": {"type": "string"},
        "watching": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["call", "confidence", "why", "watching", "evidence_quote"],
}

PROMPT = """You are reviewing an OPEN position in an automated trading system.

THE RULES IT RUNS UNDER, generated from the live policy so they cannot be stale:
{rules}

WHAT THIS SYSTEM DID IN THE LAST 24 HOURS, and what it has learned overall:
{recent}

{memory}

Your only question: is there a reason to leave EARLY, before any of those fire?

What that means here. The money in this strategy comes from the minority of
trades that reach target; most stop out. A position drifting against you is
ordinary and is NOT a reason to exit -- the stop already prices that. A reason
to exit early is that the REASON FOR THE TRADE has stopped being true: the
structure that justified it has broken, or news has appeared that makes the
price action untrustworthy.

Be reluctant. Exiting early on noise converts a strategy that wins big
occasionally into one that wins small rarely, which is how it loses money.

call: hold, or exit_early.
confidence: strong, neutral, or weak.
why: one sentence naming the specific fact behind the call.
watching: the one observable thing that would change your mind, named from the
  data shown -- not a feeling.
evidence_quote: a span copied VERBATIM from this company's headlines, or an
  empty string if none apply. Anything not verbatim is discarded.

Report no prices or percentages of your own.

{block}
"""


def block_for(symbol, position, bars, ctx, daily, headlines) -> str:
    entry = float(position["avg_entry_price"])
    now = float(position["current_price"])
    lines = [f"[{symbol}]", "",
             "THE POSITION",
             f"  shares                {position['qty']}",
             f"  entry                 {entry:.2f}",
             f"  now                   {now:.2f}   "
             f"({(now / entry - 1) * 100:+.2f}%)",
             f"  unrealised            {float(position['unrealized_pl']):+,.2f}",
             "", "30-MINUTE INDICATORS SINCE ENTRY"]
    for name in ("rsi14", "macd_histogram", "atr_pct", "volume_ratio",
                 "ema50_slope_pct"):
        value = getattr(ctx, name, None)
        if value is not None:
            lines.append(f"  {name:<21}{value:>10.3f}")
    for name in ("ema9", "ema20", "ema50", "ema200"):
        value = getattr(ctx, name, None)
        if value:
            side = "above" if ctx.price >= value else "below"
            lines.append(f"  {name:<21}{value:>10.2f}   price {side}")
    if daily:
        lines += ["", "DAILY TREND"]
        for label, value in daily.items():
            lines.append(f"  {label:<21}{value:>10}")
    lines += ["", "RECENT HEADLINES"]
    lines += [f"  - {h}" for h in headlines[:5]] or ["  (none)"]
    return "\n".join(lines)


async def main(model: str) -> int:
    async with httpx.AsyncClient(timeout=600) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key,
                        client=client, paper=True)
        try:
            positions = await broker._get("/v2/positions")
        except Exception as exc:                             # noqa: BLE001
            print(f"{RED}broker unreachable: {exc}{RESET}")
            return 2
        if not positions:
            print(f"{DIM}no open positions{RESET}")
            return 0

        # Built once per run, not per position: it is the same facts for all
        # of them, and rebuilding it would invite the model to think the
        # picture changed between two reviews in the same scan.
        from scanner.llm import memory
        recent = memory.last_24h(settings.audit_log_path)
        learned = memory.build_block(settings.audit_log_path)

        names = tuple(p["symbol"] for p in positions)
        try:
            benchmark = technicals.compute(
                await fetch_bars("^GSPC", client, range_="2y"))
            daily_bars, _ = await fetch_many(names, client, range_="2y")
        except Exception:                                    # noqa: BLE001
            benchmark, daily_bars = None, {}

        for position in positions:
            symbol = position["symbol"]
            bars = history.accumulated(symbol, V.interval_minutes,
                                       SESSION_ALIGNED)
            if bars is None or len(bars) < 30:
                print(f"  {YELLOW}{symbol}: no bars{RESET}")
                continue
            ctx = context_of(bars)
            daily: dict[str, str] = {}
            series = daily_bars.get(symbol)
            if series is not None and benchmark is not None:
                features = technicals.compute(series)
                strength = technicals.relative_strength(features, benchmark)
                if strength is not None:
                    daily["12-1 vs S&P (pp)"] = f"{strength:+.1f}"
                if features.sma200:
                    daily["vs 200-day (%)"] = (
                        f"{(series.closes[-1] / features.sma200 - 1) * 100:+.1f}")
            try:
                headlines = [n.title for n in await yahoo_rss.fetch(symbol,
                                                                    client)]
            except Exception:                                # noqa: BLE001
                headlines = []

            block = block_for(symbol, position, bars, ctx, daily, headlines)
            print(f"\n{BOLD}{'=' * 68}{RESET}")
            print(block)
            try:
                out = await chat_json(PROMPT.format(rules=V.as_prose(),
                                  recent=recent, memory=learned,
                                  block=block), SCHEMA,
                                      client, model=model)
            except Exception as exc:                         # noqa: BLE001
                print(f"  {YELLOW}model unavailable: {exc}{RESET}")
                continue

            quote = str(out.get("evidence_quote", "")).strip()
            from scanner.llm.grounding import _collapse
            grounded = not quote or any(
                _collapse(quote) in _collapse(h) for h in headlines)
            call = str(out.get("call", "hold"))
            tint = RED if call == "exit_early" else GREEN
            print(f"\n  {BOLD}GEMMA{RESET}  {tint}{call.upper()}{RESET}"
                  f"   confidence {out.get('confidence')}"
                  f"   {'grounded' if grounded else YELLOW + 'ungrounded' + RESET}")
            print(f"    why       {out.get('why', '')}")
            print(f"    watching  {out.get('watching', '')}")
            if quote:
                print(f"    quote     {quote[:64]}")

            append_event(settings.audit_log_path, AuditEvent(
                event="position_review", at=datetime.now().astimezone(),
                symbol=symbol, build=build_id(), policy=V.fingerprint,
                stage="shadow",
                reason=f"{call} ({out.get('confidence')}, "
                       f"{'grounded' if grounded else 'ungrounded'}): "
                       f"{out.get('why', '')}"[:300],
            ))

    print(f"\n{DIM}Recorded, not enforced. Nothing was closed and there is no "
          f"path here that could close anything. Replay these against realised "
          f"outcomes before giving the call any authority.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=settings.triage_model)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
