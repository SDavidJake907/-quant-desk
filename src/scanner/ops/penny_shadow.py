"""The penny watch-list shadow: what a $1-$5 book would buy each day. It NEVER orders.

Operator 2026-10-27, after the TradingView penny list (sub-$0.30, ~3 of ~100 profitable, -30 to
-50% days): "keep it, running as a shadow.. no trades". Each day after the close
(scripts/penny_shadow.py, in the outcomes task): NYSE / Nasdaq / AMEX common stocks priced $1-$5,
at least $5M traded a day, 6-month return up, above the 50-day, a positive operating margin, no
reverse split in the last year, while IWM is above its 200-day; the 4 strongest by 12-1 momentum
are recorded. Each record is graded 5 and 21 sessions on: the picks' average return minus IWM's.
Promotion to money only by the operator, after it beats IWM over months (FREEZE).

PURE: values in, values out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

LOW, HIGH = 1.0, 5.0
MIN_DOLLAR_VOLUME = 5_000_000.0
WINDOW = 20
PICKS = 4
HORIZONS = (5, 21)
EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"})
#: Names that are not operating companies: funds, warrants, rights, units, preferreds.
NOT_COMMON = re.compile(r"\b(ETF|ETN|Fund|Trust|Warrants?|Rights?|Units?|Preferred|Notes?|Depositary Shares)\b",
                        re.IGNORECASE)


def is_common_stock(asset: Mapping[str, Any]) -> bool:
    symbol = str(asset.get("symbol") or "")
    return (asset.get("status") == "active" and bool(asset.get("tradable"))
            and asset.get("exchange") in EXCHANGES and symbol.isalpha() and len(symbol) <= 5
            and not NOT_COMMON.search(str(asset.get("name") or "")))


def in_band(price: float | None) -> bool:
    return price is not None and LOW <= price <= HIGH


def passes(closes: Sequence[float], volumes: Sequence[float], *, operating_margin: float | None,
           reverse_split: bool) -> str | None:
    """Why a name is left out, or None. Needs 127 closes (6 months)."""
    if len(closes) < 127 or len(volumes) < WINDOW:
        return "under 6 months of prices"
    traded = sum(c * v for c, v in zip(closes[-WINDOW:], volumes[-WINDOW:])) / WINDOW
    if traded < MIN_DOLLAR_VOLUME:
        return f"traded ${traded / 1e6:.1f}M a day (< ${MIN_DOLLAR_VOLUME / 1e6:g}M)"
    if closes[-1] <= closes[-127]:
        return "6-month return not positive"
    if closes[-1] <= sum(closes[-50:]) / 50:
        return "not above its 50-day average"
    if operating_margin is None or operating_margin <= 0:
        return "operating margin not positive (or unknown)"
    if reverse_split:
        return "reverse split in the last year"
    return None


def top(momentum: Mapping[str, float | None], n: int = PICKS) -> list[str]:
    """The `n` strongest positive 12-1 momentum names; unknown or negative momentum never picked."""
    known = [(v, s) for s, v in momentum.items() if v is not None and v > 0]
    return [s for _, s in sorted(known, key=lambda x: (-x[0], x[1]))][:n]


def excess(record: Mapping[str, Any], closes_now: Mapping[str, float]) -> float | None:
    """The picks' average % return minus IWM's, from the record's closes to `closes_now`;
    None when there were no picks or any price is missing."""
    picks = record.get("picks") or []
    if not picks or "IWM" not in closes_now or not record.get("iwm_close"):
        return None
    rets = []
    for p in picks:
        now = closes_now.get(p["symbol"])
        if now is None or not p.get("close"):
            return None
        rets.append((now / float(p["close"]) - 1) * 100)
    iwm = (closes_now["IWM"] / float(record["iwm_close"]) - 1) * 100
    return round(sum(rets) / len(rets) - iwm, 6)
