"""Gemma reviews every open position, every scan, and its call is recorded. MONITOR V2.

    python scripts/review_positions.py
    python scripts/review_positions.py --model qwen2.5:14b-instruct

READ-ONLY AGAINST THE BROKER. It reads positions and writes to the audit log.
There is no --execute and no close path. That is the point.

WHAT WATCHES A POSITION
The bracket's stop and target (held by the broker), `protect.py` (re-arms a
missing stop, sells a breached one), `manage_positions.py` (60-bar limit),
`flatten.py` (weekends and holidays). All mechanical. This adds the one thing
they cannot do: read the news against the trade.

V2 (2026-10-06): THE MATH IN, THE NOISE OUT
V1 gave Gemma RSI, MACD, EMAs and % P&L -- all measured useless -- and not the
trade's own stop, target or R, and it said "hold" every time. V2 gives it the
trade math as codes it did not compute (scanner/monitor/codes.py: R, fair
P(target), MFE/MAE, cushion in 30m and daily ATRs, bars used, sessions to the
flatten) and headlines from the last 48h, and asks one question: is the REASON
for the position broken? Settings in scanner/monitor/review.py: temperature 0,
fixed seed, JSON schema, red flag only with a verbatim quote and a material
event. When no new headline has appeared since the last review, the previous
call is reused and recorded as such -- the GPU belongs to the scanner.

WHY THIS IS RECORDED AND NOT ENFORCED
Gemma's own exit plans measured WORSE than holding (p=0.021), and the replay
on 2026-10-06 showed a trade's state does not predict its ending. A red flag
earns authority only through TRIALS.md #45 (30+ flags, flagged worse than
unflagged, t >= 2) and then only level 3: tighten the stop or sell half,
never a full exit on Gemma alone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.audit import append_event, submitted_levels
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.features import history
from scanner.features.intraday import EXCHANGE_TZ, SESSION_ALIGNED
from scanner.llm.ollama import coerce_keep_alive
from scanner.models import AuditEvent
from scanner.monitor import codes, review
from scanner.patterns import live as patterns
from scanner.features.bars import fetch_bars
from scanner.sources import yahoo_rss

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
STATE = Path("data/ops/review_state.json")


def _ts(stamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    except ValueError:
        return None


async def fill_times(broker) -> dict[str, datetime]:
    """Latest filled BUY per symbol: when the position now held was opened."""
    orders = await broker._get("/v2/orders", status="closed", limit=200, direction="desc")
    out: dict[str, datetime] = {}
    for o in orders or []:
        when = _ts(o.get("filled_at"))
        if o.get("side") == "buy" and when and o["symbol"] not in out:
            out[o["symbol"]] = when
    return out


def code_line(position, levels, filled_at) -> str | None:
    symbol = position["symbol"]
    bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
    if symbol not in levels or filled_at is None or bars is None or len(bars) < 30:
        return None
    stop, target = levels[symbol]
    since = [i for i, t in enumerate(bars.times) if t + timedelta(minutes=V.interval_minutes) > filled_at]
    price = float(position["current_price"])
    today = datetime.now(tz=EXCHANGE_TZ)
    return codes.render(codes.swing(
        entry=float(position["avg_entry_price"]), stop=stop, target=target, price=price,
        highs=[bars.highs[i] for i in since], lows=[bars.lows[i] for i in since],
        atr30=codes.wilder_atr(bars.highs, bars.lows, bars.closes) or 0.0,
        atr_daily=codes.daily_atr(bars.times, bars.highs, bars.lows, bars.closes) or 0.0,
        bars_held=len(since), max_bars=V.max_hold_bars,
        sessions_to_flatten=max(0, 4 - today.weekday())))


async def main(model: str) -> int:
    async with httpx.AsyncClient(timeout=600) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client=client, paper=True)
        # NOTHING TO REVIEW WHILE THE MARKET IS SHUT (14 Saturday reviews on
        # 2026-10-03 of a price that could not move). A failed clock read reviews anyway.
        try:
            if not await broker.market_open():
                print(f"{DIM}market closed — no review{RESET}")
                return 0
        except Exception:                                    # noqa: BLE001
            pass
        try:
            positions = await broker._get("/v2/positions")
            filled = await fill_times(broker)
        except Exception as exc:                             # noqa: BLE001
            print(f"{RED}broker unreachable: {exc}{RESET}")
            return 2
        if not positions:
            print(f"{DIM}no open positions{RESET}")
            return 0

        levels = submitted_levels(settings.audit_log_path)
        pattern_stats = patterns.load_stats()
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        now = datetime.now(timezone.utc)
        for position in positions:
            symbol = position["symbol"]
            line = code_line(position, levels, filled.get(symbol)) or "no-levels"
            try:
                items = [(n.title, n.published_at) for n in await yahoo_rss.fetch(symbol, client)
                         if n.published_at]
            except Exception:                                # noqa: BLE001
                items = []
            heads = review.recent_headlines(items, now=now)
            try:
                d = await fetch_bars(symbol, client, range_="3mo")
                pattern = patterns.current(d.closes, d.highs, d.lows, pattern_stats)
            except Exception:                                # noqa: BLE001
                pattern = None
            previous = state.get(symbol)
            if previous and previous.get("heads") == heads:
                out, reused = previous["out"], True
            else:
                body = review.payload(
                    review.prompt("swing trade with a stop and a target", line, heads,
                                  "the stop already prices that", pattern),
                    model=model, num_ctx=settings.triage_num_ctx,
                    keep_alive=coerce_keep_alive(settings.llm_keep_alive), think=settings.llm_think)
                try:
                    out = review.validate(await review.ask(client, settings.ollama_host, body,
                                                           timeout=settings.llm_timeout_s), heads)
                except Exception as exc:                     # noqa: BLE001
                    print(f"  {YELLOW}{symbol}: model unavailable: {exc}{RESET}")
                    continue
                reused = False
                state[symbol] = {"heads": heads, "out": out}
            tint = RED if out["f"] == "2" else YELLOW if out["f"] == "1" else GREEN
            print(f"  {BOLD}{symbol:<6}{RESET} {line}  {tint}c{out['c']} f{out['f']} k{out['k']} "
                  f"t={out.get('t', 'none')}{RESET}{' (reused)' if reused else ''}  h{len(heads)}")
            append_event(settings.audit_log_path, AuditEvent(
                event="position_review", at=datetime.now().astimezone(), symbol=symbol,
                build=build_id(), policy=V.fingerprint, stage="shadow", model=model,
                reason=review.reason(out, f"{line} | {pattern or 'PT=none'}", reused=reused)))
        held = {p["symbol"] for p in positions}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({s: v for s, v in state.items() if s in held}), encoding="utf-8")

    print(f"{DIM}Recorded, not enforced. Nothing can be closed from here; red flags are "
          f"scored under TRIALS.md #45 before they may act.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=settings.triage_model)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.model)))
