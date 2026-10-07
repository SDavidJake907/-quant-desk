"""Every opinion the system can show, ranked by what it has proved (WORKLIST §38).

THREE TIERS
  PRIMARY    measured out of sample and acted on: the momentum rule.
  UNPROVEN   advisory; never graded. Every call is logged and graded against
             the S&P over a fixed horizon; it graduates to PRIMARY consideration
             only by clearing GRADUATION, set before the first grade.
  NOISE      advisory; already tested here on thousands of events with no edge.
             Shown, labelled, never graded again -- a few dozen live calls
             cannot overturn 23,000-143,000 historical ones, and a lucky streak
             would only look like a comeback.

Nothing in an advisory tier may block, size or close a trade. The tiers exist
so a disagreement reads as a disagreement, not as a reason to act.

PURE, except `momentum_picks`, which reads the momentum record from disk.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scanner.models import Frozen

PRIMARY = "primary"
UNPROVEN = "advisory - unproven"
NOISE = "advisory - tested, no edge"

#: Pre-set before any grade exists (2026-10-06). 100 graded calls and t >= 2
#: on direction-signed 21-day excess return; two opinions under test, so a
#: looser bar would let one pass by luck.
GRADUATION_CALLS = 100
GRADUATION_T = 2.0
HORIZON_DAYS = 21


class Opinion(Frozen):
    key: str
    label: str
    tier: str
    evidence: str


OPINIONS: tuple[Opinion, ...] = (
    Opinion(key="momentum", label="Momentum rule (12-1, top 10, S&P trend filter)",
            tier=PRIMARY,
            evidence="+2.38pp/3-month hold over random 2016-26, t 1.49; crash "
                     "drawdown 12.7% 2000-15 (WORKLIST §31, guard arm A)"),
    Opinion(key="tradingagents", label="TradingAgents (market + news analysts)",
            tier=UNPROVEN,
            evidence="graded from 2026-10-05; its inputs (indicators, news) were "
                     "noise when tested alone"),
    Opinion(key="gemma_review", label="Gemma news read on momentum holdings",
            tier=UNPROVEN,
            evidence="graded from 2026-10-06; news labels did not predict "
                     "sweep outcomes (news_event_test)"),
    Opinion(key="rsi", label="RSI(14) daily", tier=NOISE,
            evidence="RSI/MACD/ADX entries worse than random, t -4.2 (§35); "
                     "|t| < 1.7 inside momentum (§31); divergence flipped"),
    Opinion(key="moving_averages", label="Price vs 50/200-day averages", tier=NOISE,
            evidence="SMA50/200 |t| < 1.7 inside momentum (§31); EMA trend "
                     "context nothing survives (§12)"),
    Opinion(key="chart_pattern", label="Chart pattern (Lo-Mamaysky-Wang)", tier=NOISE,
            evidence="143,586 events, no measured edge; bullish and bearish "
                     "names return the same (§34)"),
    Opinion(key="sweep", label="Sweep scanner setup", tier=NOISE,
            evidence="-0.041R/trade on unseen 2018-24 (§30); entries paused"),
)
BY_KEY = {o.key: o for o in OPINIONS}

#: TradingAgents' five-point scale and the trader's three.
_TA_DIRECTION = {"buy": 1, "overweight": 1, "hold": 0,
                 "underweight": -1, "sell": -1}


def ta_direction(reason: str) -> int | None:
    """'Overweight; trader=Buy; ...' -> +1. The portfolio manager's rating
    (first field) is the call; None when it cannot be read."""
    head = (reason or "").split(";", 1)[0].strip().lower()
    return _TA_DIRECTION.get(head)


def gemma_direction(reason: str) -> int | None:
    """'hold (strong, grounded): ...' -> +1 (stay long); 'exit ...' -> -1."""
    head = (reason or "").strip().lower()
    if head.startswith("hold"):
        return 1
    if head.startswith(("exit", "sell", "red")):
        return -1
    return None


#: audit event -> (opinion key, direction parser)
SOURCES = {"tradingagents_rating": ("tradingagents", ta_direction),
           "momentum_review": ("gemma_review", gemma_direction)}


def latest_calls(rows: Iterable[Mapping[str, Any]], symbol: str) -> dict[str, dict]:
    """The newest call per graded opinion for `symbol`, from the audit log."""
    out: dict[str, dict] = {}
    for row in rows:
        source = SOURCES.get(str(row.get("event")))
        if not source or str(row.get("symbol", "")).upper() != symbol:
            continue
        key, parse = source
        out[key] = {"at": str(row.get("at", ""))[:10],
                    "direction": parse(str(row.get("reason", ""))),
                    "call": str(row.get("reason", "")).split(";", 1)[0].split(":", 1)[0]}
    return out


def momentum_picks(paths: Iterable[Path] = (Path("data/momentum/paper.jsonl"),
                                            Path("data/momentum/dry_run.jsonl"))
                   ) -> tuple[str, tuple[str, ...], bool | None]:
    """(date, picks, invested) of the newest momentum rebalance on record --
    the paper book if it has one, else the dry run."""
    for path in paths:
        if path.exists():
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                last = json.loads(lines[-1])
                return (str(last.get("date", "")), tuple(last.get("picks") or ()),
                        last.get("invested"))
    return ("", (), None)


def snapshot(symbol: str, rows: Iterable[Mapping[str, Any]],
             picks: Iterable[str]) -> dict[str, Any]:
    """What a trade was entered beside: the primary call and the latest of
    each unproven opinion. Logged by manual_trade.py so a later question --
    did the operator do better when following TradingAgents? -- has data."""
    in_picks = symbol in set(picks)
    calls = latest_calls(rows, symbol)
    disagree = [k for k, c in calls.items()
                if in_picks and c.get("direction") == -1]
    return {"symbol": symbol,
            "momentum": "top-10 pick" if in_picks else "not a pick",
            "opinions": calls,
            "disagrees_with_primary": disagree}
