"""Daily check of the momentum holdings: the math as codes, Gemma reads the news.

    python scripts/momentum_watch.py            # after the close
    python scripts/momentum_watch.py --force    # allow during market hours

READ-ONLY. Reads the dry-run book (data/momentum/dry_run_book.json); nothing
here buys or sells. For each holding, Python computes (scanner/monitor/codes.py)
  K rank percentile now   W weight %   S sector share %   T S&P gap to 210d %
  L 20-day $vol $M        M 12-1 momentum %
and Gemma, with the monitor-v2 settings (temperature 0, fixed seed, schema,
red flag only with a verbatim quote and a material event), says whether
48h of headlines break the reason for holding. Momentum positions have no
stop and Gemma never exits them: the monthly rebuild does. A red flag is
recorded (data/momentum/watch.jsonl, audit event momentum_review) for
TRIALS.md #45, and shown at the top of the output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.audit import append_event
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import compute
from scanner.llm.ollama import coerce_keep_alive
from scanner.models import AuditEvent
from scanner.momentum import state as mstate
from scanner.monitor import codes, review
from scanner.patterns import live as patterns
from scanner.sources import composite, yahoo_rss
from scanner.sources.index_history import SP400, SP500

BOOK = Path("data/momentum/dry_run_book.json")
WATCH = Path("data/momentum/watch.jsonl")


async def main(force: bool) -> int:
    now = datetime.now(timezone.utc)
    et = now.astimezone(EXCHANGE_TZ)
    if et.weekday() < 5 and dtime(9, 20) <= et.time() <= dtime(16, 15) and not force:
        print("market hours: the scanner owns the model. Run after the close, or --force.")
        return 1
    book = mstate.load(BOOK)
    if book is None:
        print("no dry-run book yet")
        return 1
    held = {s: q for t in book.tranches for s, q in t.holdings.items()}
    if not held:
        print("book holds nothing")
        return 0
    async with httpx.AsyncClient(timeout=600) as client:
        members = await composite.load_universe(client, (SP500, SP400))
        sector = {m.symbol: m.sector for m in members}
        daily, _ = await fetch_many(tuple(sorted(set(sector) | set(held))), client, range_="2y")
        bench = await fetch_bars("^GSPC", client, range_="2y")
        scores = {s: compute(b).momentum_12_1 for s, b in daily.items() if s in sector}
        ranked = [s for _, s in sorted(((v, s) for s, v in scores.items() if v is not None), reverse=True)]
        rank = {s: i + 1 for i, s in enumerate(ranked)}
        price = {s: daily[s].closes[-1] for s in held if s in daily}
        total = sum(t.cash for t in book.tranches) + sum(q * price[s] for s, q in held.items())
        invested = sum(q * price[s] for s, q in held.items())
        by_sector: dict[str, float] = {}
        for s, q in held.items():
            by_sector[sector.get(s, "?")] = by_sector.get(sector.get(s, "?"), 0) + q * price[s]
        gap = (bench.closes[-1] / statistics.fmean(bench.closes[-210:]) - 1) * 100
        rows = []
        pattern_stats = patterns.load_stats()
        for s, q in sorted(held.items()):
            b = daily[s]
            line = codes.render(codes.momentum(
                rank=rank.get(s, len(ranked)), universe=len(ranked), weight_pct=q * price[s] / total * 100,
                sector_share_pct=by_sector[sector.get(s, "?")] / invested * 100, trend_gap_pct=gap,
                dollar_volume=statistics.fmean(c * v for c, v in zip(b.closes[-20:], b.volumes[-20:])),
                mom_12_1=scores.get(s) or 0.0))
            try:
                items = [(n.title, n.published_at) for n in await yahoo_rss.fetch(s, client) if n.published_at]
            except Exception:                                   # noqa: BLE001
                items = []
            heads = review.recent_headlines(items, now=now)
            pattern = patterns.current(b.closes, b.highs, b.lows, pattern_stats)
            body = review.payload(review.prompt("momentum holding, no stop, rebuilt monthly", line, heads,
                                                "the monthly rebuild handles fading momentum", pattern),
                                  model=settings.triage_model, num_ctx=settings.triage_num_ctx,
                                  keep_alive=coerce_keep_alive(settings.llm_keep_alive), think=settings.llm_think)
            try:
                out = review.validate(await review.ask(client, settings.ollama_host, body,
                                                       timeout=settings.llm_timeout_s), heads)
            except Exception as exc:                            # noqa: BLE001
                print(f"  {s}: model unavailable: {exc}")
                continue
            rows.append((s, f"{line} | {pattern or 'PT=none'}", out, len(heads)))
            append_event(settings.audit_log_path, AuditEvent(
                event="momentum_review", at=datetime.now().astimezone(), symbol=s, stage="shadow",
                model=settings.triage_model, reason=review.reason(out, line, reused=False)))
    flags = [r for r in rows if r[2]["f"] == "2"]
    print(f"momentum watch {et:%Y-%m-%d}  T{gap:+.1f}  holdings {len(rows)}  red flags {len(flags)}")
    for s, line, out, h in sorted(rows, key=lambda r: r[2]["f"], reverse=True):
        print(f"  {s:<6} {line:<42} f{out['f']} k{out['k']} t={out.get('t', 'none'):<14} h{h}")
    WATCH.parent.mkdir(parents=True, exist_ok=True)
    with WATCH.open("a", encoding="utf-8") as handle:
        for s, line, out, h in rows:
            handle.write(json.dumps({"date": et.date().isoformat(), "symbol": s, "codes": line,
                                     "f": out["f"], "k": out["k"], "t": out.get("t"), "e": out.get("e"),
                                     "grounded": out["grounded"], "price": price.get(s), "heads": h}) + "\n")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args().force)))
