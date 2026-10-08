"""Grade selection skill across the whole menu (rules in scanner/ops/selection.py).

    python scripts/selection_grade.py

Gemma: every SUBMITTED morning decision -- the names it chose against the names
it passed over, 5 and 21 sessions on. TradingAgents: the rank correlation (IC)
of each morning's ratings with what the stocks then did. Runs in the 13:30 AK
outcomes task; writes logs/competition/selection.md (for the operator) and
data/competition/selection.json (read by Gemma's morning scoreboard).
READ-ONLY.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import opinions
from scanner.config import settings
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_many
from scanner.ops import selection as sel

HORIZONS = (5, 21)
OUT_MD, OUT_JSON = Path("logs/competition/selection.md"), Path("data/competition/selection.json")
GEMMA_LOG = Path("logs/scheduled_gemma.log")


def menu_from_log(day: date) -> list[str]:
    """Fallback for decisions logged before the menu was recorded (2026-10-07):
    the 'may choose from [...]' line of that morning's scheduled run."""
    if not GEMMA_LOG.exists():
        return []
    stamp, taking = f"{day:%m/%d/%Y}", False
    for line in GEMMA_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("===== GEMMA"):
            taking = stamp in line
        elif taking and "may choose from" in line:
            return re.findall(r"'([A-Z.]+)'", line.split("may choose from", 1)[1])
    return []


def gemma_decisions(rows) -> list[tuple[date, list[str], list[str]]]:
    out = {}
    for r in rows:
        if r.get("event") != "gemma_decision":
            continue
        try:
            body = json.loads(r.get("reason", "{}"))
        except ValueError:
            continue
        if not body.get("submitted"):
            continue                         # previews and rehearsals are not decisions
        day = date.fromisoformat(str(r["at"])[:10])
        menu = body.get("menu") or menu_from_log(day)
        if menu and body.get("keep"):
            out[day] = (day, list(body["keep"]), menu)
    return [out[d] for d in sorted(out)]


def ta_calls(rows) -> dict[date, dict[str, int]]:
    out: dict[date, dict[str, int]] = defaultdict(dict)
    for r in rows:
        if r.get("event") == "tradingagents_rating":
            d = opinions.ta_direction(str(r.get("reason", "")))
            if d is not None:
                out[date.fromisoformat(str(r["at"])[:10])][str(r["symbol"]).upper()] = d
    return out


async def main() -> int:
    rows = read_rows(settings.audit_log_path)
    decisions, calls = gemma_decisions(rows), ta_calls(rows)
    symbols = {s for _, _, m in decisions for s in m} | {s for c in calls.values() for s in c}
    async with httpx.AsyncClient(timeout=120) as client:
        bars, _ = await fetch_many(tuple(sorted(symbols)), client, range_="6mo") if symbols else ({}, ())
    ohlc = {s: list(zip(b.dates, b.opens, b.closes)) for s, b in bars.items()}

    result = {"gemma": {}, "tradingagents": {}, "updated": date.today().isoformat()}
    lines = ["# Selection skill", "",
             "Graded across the whole menu (Jansen ch. 4-5): Gemma's chosen names vs the ones it "
             "passed over; TradingAgents' ratings vs what the stocks did. Overlapping horizons make "
             "the t-statistic look surer than it is.", ""]
    for h in HORIZONS:
        spreads = []
        for day, keep, menu in decisions:
            fwd = {s: v for s in menu if s in ohlc
                   and (v := sel.forward_return(ohlc[s], day, h)) is not None}
            spreads.append(sel.spread(keep, menu, fwd))
        g = sel.summarize(spreads)
        ics = []
        for day, c in sorted(calls.items()):
            pairs = [(d, v) for s, d in c.items() if s in ohlc
                     and (v := sel.forward_return(ohlc[s], day, h)) is not None]
            ics.append(sel.spearman([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None)
        t = sel.summarize(ics)
        result["gemma"][str(h)] = g.model_dump()
        result["tradingagents"][str(h)] = t.model_dump()
        lines += [f"## {h} sessions ahead", "",
                  f"- **Gemma**, chosen minus passed-over: "
                  + (f"{g.mean:+.2f} pts on average over {g.days} graded days, positive on "
                     f"{g.positive_days} (t {g.t:+.2f})" if g.days else "no day graded yet"),
                  f"- **TradingAgents**, rank correlation of its calls with returns: "
                  + (f"{t.mean:+.3f} on average over {t.days} graded days, positive on "
                     f"{t.positive_days} (t {t.t:+.2f})" if t.days else "no day graded yet"), ""]
    pending = len(decisions) - result["gemma"]["5"]["days"]
    lines.append(f"_{len(decisions)} Gemma decisions on record, {pending} waiting for their first "
                 f"5 sessions; {len(calls)} days of TradingAgents ratings._")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
