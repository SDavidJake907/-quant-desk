"""Which events reach the operator's phone, and how they read.

The trading scripts already record everything that matters in the audit log;
this only chooses and words it. Routine records (reviews, slates, ratings)
stay off the phone -- an alert channel that buzzes for everything gets muted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from scanner.models import Frozen


class Alert(Frozen):
    title: str
    message: str
    priority: str = "default"
    tags: tuple[str, ...] = ()


def _one(row: Mapping) -> Alert | None:
    event, sym, why = row.get("event", ""), row.get("symbol") or "", str(row.get("reason") or "")
    if event == "order_submitted":
        return Alert(title=f"Order sent: {sym}", message=f"{sym} entry order submitted. {why}".strip(),
                     tags=("inbox_tray",))
    if event == "trade_outcome":
        return Alert(title=f"Trade closed: {sym}", message=why or f"{sym} closed", tags=("checkered_flag",))
    if event == "protection_closed":
        stop = "close_stop" in why
        return Alert(title=f"{'Stop' if stop else 'Target'} taken by protect: {sym}", message=why,
                     priority="high", tags=("warning",))
    if event in ("protection_failed", "position_unprotected"):
        return Alert(title=f"UNPROTECTED: {sym}", message=why or f"{sym} has no live stop",
                     priority="urgent", tags=("rotating_light",))
    if event == "protection_restored":
        return Alert(title=f"Stop re-armed: {sym}", message=why, tags=("shield",))
    if event == "weekend_flatten" and "flat=False" in why:
        return Alert(title="Weekend flatten FAILED", message=why, priority="urgent",
                     tags=("rotating_light",))
    if event == "news_gate_degraded":
        return Alert(title="News gate failing open", message=why, priority="high", tags=("warning",))
    if event == "risk_breach":
        return Alert(title="Account risk limit reached", message=why, priority="urgent",
                     tags=("rotating_light",))
    return None


def from_audit(rows: Iterable[Mapping]) -> list[Alert]:
    return [a for a in (_one(r) for r in rows) if a is not None]


def from_fills(activities: Iterable[Mapping]) -> list[Alert]:
    out = []
    for a in activities:
        if a.get("activity_type") != "FILL":
            continue
        side, sym, qty, price = a.get("side", ""), a.get("symbol", ""), a.get("qty", ""), a.get("price", "")
        out.append(Alert(title=f"Filled: {side} {sym}",
                         message=f"{side} {qty} {sym} @ {float(price):.2f}" if price else f"{side} {qty} {sym}",
                         tags=("moneybag",)))
    return out
