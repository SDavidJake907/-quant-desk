"""Gemma's stock pool: the whole S&P 500, the way the scanner sees it.

Operator 2026-10-19: "lets give her atleast 500 ... like its in the stock
scanner... stock pool. how it was running."

ROUND 1 (this module): every S&P 500 stock that passes the scanner's
tradeability floor (policy min_price, min_range_pct, min_dollar_volume -- the
same three numbers trade.py and the ghost scanner use, here over 20 daily
bars) and that no other book holds, one compact line each: sector, 1/3-month
return, 12-1 momentum, daily ATR, and the momentum rule's rank. She shortlists
up to SHORTLIST names from it. Full blocks for 500 names would be ~150k tokens.

ROUND 2 (scripts/gemma_trade.py, unchanged rules): full facts -- news,
earnings, the brief -- for her shortlist plus her holdings; she decides there,
with the usual evidence rule. If round 1 fails, round 2 falls back to the old
hand lists (core + gemma + the momentum rule), so a bad answer never empties
her menu.

PURE: numbers in, text and validation out.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scanner.models import Frozen

#: Off = back to the hand lists (core + gemma + the momentum rule) only.
ENABLED = True
SHORTLIST = 20
WINDOW = 20
#: Operator 2026-10-19: "increase the market cap to 2B" -- no stock under $2B
#: in her pool (sources/market_caps.py). Every S&P 500 member is far above it
#: today; it bites once smaller universes join the pool.
MIN_MARKET_CAP = 2_000_000_000.0
#: MidCap 400 joins her pool (operator 2026-10-21, "lets do all 3", the step
#: queued for after the Oct 31 verdict): from MIDCAP_FROM, the strongest
#: MIDCAP_MAX mid-caps by 12-1 momentum (positive only) that pass the same
#: floor and the cap floor. Capped so round 1 stays inside her 32k context
#: (~35 tokens a line: 488 S&P lines measured 17,152 tokens; +120 is ~+4,200).
#: Before MIDCAP_FROM her pool and prompt are exactly as they were.
#: Brought forward from 11-02 to 10-22 and widened to the SmallCap 600 too
#: (operator 2026-10-22, "widen Gemma stock pool" -> "add 120 mid/small now"):
#: the 120 strongest across both indexes, same floors, same $2B cap floor.
MIDCAP_FROM = date(2026, 10, 22)
MIDCAP_MAX = 120
#: Each round 1 saves the pool it showed her, one file a day (the last run that
#: day wins). The breakout book (execution/orb.py) trades from it, so it never
#: builds a second universe (operator 2026-10-23: "tie this in" with her pool).
POOL_DIR = Path("data/gemma/pool")
#: How far ahead the step-1 lines look for a report date.
EARNINGS_SHOWN_DAYS = 30


class PoolRow(Frozen):
    symbol: str
    sector: str
    r1m: float | None
    r3m: float | None
    mom_12_1: float | None
    atr_pct: float | None
    momentum_rank: int | None = None
    #: Filled only for the rows she is shown after the buy-rule filter (Gemma 2026-10-26, A1):
    #: % above the 50-day, and the next report ("YYYY-MM-DD", "none" or "unknown").
    vs_50d: float | None = None
    earnings: str | None = None


def tradeable(closes: Sequence[float], highs: Sequence[float], lows: Sequence[float],
              volumes: Sequence[float], *, min_price: float, min_range_pct: float,
              min_dollar_volume: float, window: int = WINDOW) -> bool:
    """The scanner's floor: price, average range, average dollar volume."""
    c, h, lo, v = closes[-window:], highs[-window:], lows[-window:], volumes[-window:]
    if not c or c[-1] < min_price:
        return False
    spans = [(hi - l) / x * 100 for hi, l, x in zip(h, lo, c) if x]
    if (sum(spans) / len(spans) if spans else 0.0) < min_range_pct:
        return False
    return sum(a * b for a, b in zip(v, c)) / len(c) >= min_dollar_volume


def large_enough(symbol: str, caps: Mapping[str, float], *,
                 floor: float = MIN_MARKET_CAP) -> bool:
    """At or above the cap floor. Unknown (no caps today, or the name missing)
    is KEPT: a data outage must never empty her pool."""
    cap = caps.get(symbol)
    return cap is None or cap >= floor


def midcap_on(today: date, *, start: date = MIDCAP_FROM) -> bool:
    return today >= start


def strongest(rows: Iterable[PoolRow], limit: int = MIDCAP_MAX) -> list[PoolRow]:
    """The `limit` rows with the strongest positive 12-1 momentum."""
    up = [r for r in rows if r.mom_12_1 is not None and r.mom_12_1 > 0]
    return sorted(up, key=lambda r: (-(r.mom_12_1 or 0.0), r.symbol))[:limit]


def _pct(x: float | None) -> str:
    return f"{x:+.0f}%" if x is not None else "n/a"


def line(r: PoolRow) -> str:
    atr = f"{r.atr_pct:.1f}%" if r.atr_pct is not None else "n/a"
    mom = f" | momentum rule #{r.momentum_rank}" if r.momentum_rank else ""
    above = f" | vs 50d {r.vs_50d:+.1f}%" if r.vs_50d is not None else ""
    report = ("" if r.earnings is None
              else f" | earnings none in {EARNINGS_SHOWN_DAYS}d" if r.earnings == "none"
              else f" | earnings {r.earnings}")
    return (f"{r.symbol} ({r.sector}) 1m {_pct(r.r1m)} 3m {_pct(r.r3m)} "
            f"12-1 {_pct(r.mom_12_1)} ATR {atr}{mom}{above}{report}")


def prompt(rows: Iterable[PoolRow], *, held: Iterable[str], market: str,
           shortlist: int = SHORTLIST, universe: str = "S&P 500 stocks") -> str:
    rows = list(rows)
    held = sorted(held)
    return "\n".join((
        "You are the desk's junior trader. Step 1 of 2: choose which stocks to look at "
        "closely today.",
        f"Below is your stock pool: all {len(rows)} tradeable {universe} no other book "
        "holds, one line each (1-month and 3-month return, 12-1 momentum, daily ATR = how "
        "far it typically moves in a day).",
        f"Shortlist up to {shortlist} stocks you might buy today. In step 2 you will see "
        "each one's news, earnings date and full facts, with your holdings "
        f"({', '.join(held) or 'none'}), and make your real decision there.",
        "Look across the WHOLE pool and every sector, not only the names you know. Do not "
        "list your holdings: they are always shown.",
        "",
        f"Market: {market}",
        "",
        "The pool:",
        *(line(r) for r in rows),
        "",
        "Answer with your shortlist and one sentence on what you were looking for.",
    ))


def schema(symbols: Iterable[str], shortlist: int = SHORTLIST) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "shortlist": {"type": "array", "maxItems": shortlist,
                          "items": {"type": "string", "enum": list(symbols)}},
            "looking_for": {"type": "string"},
        },
        "required": ["shortlist", "looking_for"],
    }


def validate(answer: Any, pool: Iterable[str], *, held: Iterable[str] = (),
             shortlist: int = SHORTLIST) -> tuple[str, ...]:
    """Her shortlist: pool names only, no holdings, no repeats, capped. Never raises;
    an unusable answer is an empty shortlist (the caller falls back)."""
    items = answer.get("shortlist") if isinstance(answer, dict) else None
    if not isinstance(items, list):
        return ()
    allowed, mine = set(pool), set(held)
    out: list[str] = []
    for s in items:
        s = str(s).upper()
        if s in allowed and s not in mine and s not in out:
            out.append(s)
    return tuple(out[:shortlist])


def rows_for(bars_by_symbol: Mapping[str, Any], sectors: Mapping[str, str], *,
             momentum: Mapping[str, float | None], atr: Mapping[str, float | None],
             momentum_rank: Mapping[str, int]) -> list[PoolRow]:
    """One row per symbol with bars, strongest 12-1 first (a listing order only:
    the one ranking this desk measured)."""
    def ret(closes: Sequence[float], back: int) -> float | None:
        return (closes[-1] / closes[-back - 1] - 1) * 100 if len(closes) > back else None

    out = [PoolRow(symbol=s, sector=sectors.get(s, "unknown"), r1m=ret(b.closes, 21),
                   r3m=ret(b.closes, 63), mom_12_1=momentum.get(s), atr_pct=atr.get(s),
                   momentum_rank=momentum_rank.get(s))
           for s, b in bars_by_symbol.items()]
    return sorted(out, key=lambda r: (r.mom_12_1 is None, -(r.mom_12_1 or 0.0), r.symbol))


def save(rows: Iterable[PoolRow], today: date, *, directory: Path = POOL_DIR) -> Path:
    """Write today's pool (every row, with its daily ATR) for the breakout book."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{today.isoformat()}.json"
    path.write_text(json.dumps([r.model_dump(mode="json") for r in rows]), encoding="utf-8")
    return path


def latest(today: date, *, max_age_days: int = 4, directory: Path = POOL_DIR) -> list[PoolRow]:
    """The newest saved pool no older than `max_age_days` (a weekend is 3), or []."""
    for back in range(max_age_days + 1):
        path = directory / f"{(today - timedelta(days=back)).isoformat()}.json"
        try:
            return [PoolRow(**r) for r in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            continue
    return []
