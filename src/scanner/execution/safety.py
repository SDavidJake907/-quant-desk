"""Read-only safety checks for the paper account.

The monitor does not cancel orders or close positions. It reports conditions
that require the strategy to stop opening new trades and an operator to look
at the broker account. Keeping these checks deterministic makes an alert
actionable instead of asking an LLM to decide whether protection exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from scanner.execution.guards import RiskLimits
from scanner.execution.ownership import MOMENTUM
from scanner.execution.reconcile import reconcile


_ACTIVE_LEGS = {"new", "held", "accepted", "pending_new", "partially_filled"}


@dataclass(frozen=True)
class Finding:
    severity: str  # critical, warning, or info
    code: str
    message: str
    symbol: str = ""


@dataclass(frozen=True)
class SafetyReport:
    findings: tuple[Finding, ...]

    @property
    def critical(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "critical")

    @property
    def healthy(self) -> bool:
        return not self.critical


def _protective_legs(order: dict) -> tuple[dict, ...]:
    return tuple(
        leg for leg in order.get("legs") or []
        if str(leg.get("status") or "").lower() in _ACTIVE_LEGS
    )


def inspect(
    account: dict,
    positions: Iterable[dict],
    orders: Iterable[dict],
    intents: dict[str, dict],
    limits: RiskLimits,
    *,
    market_open: bool | None = None,
    kill_switch: bool = False,
    owner_map: Mapping[str, str] | None = None,
) -> SafetyReport:
    """Inspect broker snapshots and return deterministic safety findings.

    `owner_map` (execution/ownership.owners) exempts the momentum book from
    the three checks it fails BY DESIGN -- it holds ~10 names on its own
    sleeve, logs no TradeIntent and carries no stop (WORKLIST §37/§40). A
    false critical every day trains the operator to ignore a real one.
    Without a map every position is checked, as before.
    """
    findings: list[Finding] = []
    equity = float(account.get("equity") or 0.0)
    status = str(account.get("status") or "UNKNOWN")
    blocked = bool(
        account.get("trading_blocked")
        or account.get("account_blocked")
        or account.get("trade_suspended_by_user")
    )
    exempt = {s for s, owner in (owner_map or {}).items() if owner == MOMENTUM}
    positions = tuple(p for p in positions if str(p.get("symbol") or "") not in exempt)
    orders = tuple(o for o in orders if str(o.get("symbol") or "") not in exempt)

    if kill_switch:
        findings.append(Finding("critical", "kill_switch", "kill switch is active"))
    if status != "ACTIVE":
        findings.append(Finding("critical", "account_status", f"account status is {status}"))
    if blocked:
        findings.append(Finding("critical", "account_blocked", "account is blocked or suspended"))
    if equity < limits.min_equity:
        findings.append(Finding("critical", "equity_floor", f"equity {equity:,.2f} is below {limits.min_equity:,.2f}"))

    start = float(account.get("last_equity") or equity)
    if start > 0:
        loss_pct = (start - equity) / start * 100.0
        if loss_pct >= limits.max_daily_loss_pct:
            findings.append(Finding("critical", "daily_loss", f"daily loss is {loss_pct:.2f}%"))

    if len(positions) > limits.max_positions:
        findings.append(Finding("critical", "position_limit", f"{len(positions)} positions exceed limit {limits.max_positions}"))

    position_symbols = {str(p.get("symbol") or "") for p in positions}
    order_symbols = {str(o.get("symbol") or "") for o in orders}
    known_symbols = {str(i.get("symbol") or "") for i in intents.values()}
    for symbol in sorted(position_symbols - known_symbols):
        findings.append(Finding("critical", "untracked_position", "broker position has no audit intent", symbol))
    for symbol in sorted(order_symbols - known_symbols):
        findings.append(Finding("warning", "untracked_order", "broker order has no audit intent", symbol))

    for position in positions:
        symbol = str(position.get("symbol") or "")
        matching = [o for o in orders if str(o.get("symbol") or "") == symbol]
        brackets = [o for o in matching
                    if str(o.get("order_class") or "").lower() in ("bracket", "oco")]
        protected = False
        for order in brackets:
            legs = list(_protective_legs(order))
            # An OCO (protect.py's re-arm) carries the target as the parent
            # order itself; a bracket's parent is the filled ENTRY, never a leg.
            if str(order.get("order_class") or "").lower() == "oco" and \
                    str(order.get("status") or "").lower() in _ACTIVE_LEGS:
                legs.append(order)
            types = {str(leg.get("type") or "").lower() for leg in legs}
            if "stop" in types and "limit" in types:
                protected = True
                break
        if not protected:
            findings.append(Finding("critical", "missing_protection", "open position lacks active stop and target legs", symbol))

    if market_open is False:
        findings.append(Finding("info", "market_closed", "market is closed; no new entries should be sent"))

    return SafetyReport(tuple(findings))


def kill_switch_active(path: str | Path) -> bool:
    return Path(path).exists()


def format_report(report: SafetyReport) -> str:
    if report.healthy:
        return "SAFETY OK"
    return "\n".join(
        f"{f.severity.upper()} {f.code}{(' [' + f.symbol + ']') if f.symbol else ''}: {f.message}"
        for f in report.findings
    )
