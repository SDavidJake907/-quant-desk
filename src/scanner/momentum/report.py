"""The monthly momentum report: the engine's picks with TradingAgents' read.

RECORD-ONLY. A rating never adds, drops or resizes a pick. Whether it should
is a pre-registered question (TRIALS.md #44): after 30+ rated picks with
completed 63-day holds, do Sell/Underweight picks do worse than Buy/Overweight
ones? Until that passes, this is a reading aid and a data set.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

CONCENTRATION_NOTE = 5     # same threshold momentum_rebalance.py prints at


def excerpt(text: str, *, limit: int = 600) -> str:
    """The executive summary if the decision has one, else its start, trimmed."""
    match = re.search(r"Executive Summary\**\s*:?\**\s*(.+?)(?:\n\s*\n\*\*|\Z)", text or "", re.S)
    body = (match.group(1) if match else text or "").strip()
    body = re.sub(r"\s+", " ", body)
    return body if len(body) <= limit else body[:limit].rsplit(" ", 1)[0] + "…"


def render(record: Mapping, ratings: Mapping[str, Mapping], sector: Mapping[str, str]) -> str:
    picks = record["picks"]
    lines = [f"# Momentum report — {record['date']}", "",
             f"Tranche {record['tranche']} rebuilt. S&P 500 {record['sp500']:,.0f} vs its 210-day "
             f"average {record['sma210']:,.0f}: **{'invested' if record['invested'] else 'CASH (trend filter off)'}**.",
             "", "> TradingAgents ratings here are **record-only**: they do not add, drop or resize "
             "any pick. Whether they should is pre-registered as TRIALS.md #44.", ""]
    top = max(record.get("sectors", {}).items(), key=lambda kv: kv[1], default=("-", 0))
    if top[1] >= CONCENTRATION_NOTE:
        lines += [f"**Concentration:** {top[1]} of {len(picks)} picks are {top[0]} — one theme, one bet.", ""]
    lines += ["| # | symbol | 12-1 mom | sector | price | TradingAgents | trader |",
              "|---|---|---|---|---|---|---|"]
    for n, sym in enumerate(picks, 1):
        r = ratings.get(sym)
        lines.append(f"| {n} | {sym} | {record['momentum'][sym]:+.0f}% | {sector.get(sym, '?')} | "
                     f"{record['prices'].get(sym, 0):,.2f} | {r['rating'] if r else 'not rated'} | "
                     f"{r['trader'] if r else '-'} |")
    lines.append("")
    for sym in picks:
        r = ratings.get(sym)
        lines += [f"## {sym}", ""]
        lines += [r["summary"] or "(no summary)" if r else "Not rated (run failed or was paused).", ""]
    return "\n".join(lines)


def rating_rows(record: Mapping, ratings: Mapping[str, Mapping]) -> list[dict]:
    """One row per rated pick: what the #44 test will need when holds complete."""
    return [{"date": record["date"], "symbol": sym, "rank": n, "momentum": record["momentum"][sym],
             "rating": ratings[sym]["rating"], "trader": ratings[sym]["trader"],
             "price": record["prices"].get(sym), "tranche": record["tranche"]}
            for n, sym in enumerate(record["picks"], 1) if sym in ratings]
