"""Read-only safety checks for the paper account.

The monitor does not cancel orders or close positions. It reports conditions
that require the strategy to stop opening new trades and an operator to look
at the broker account. Keeping these checks deterministic makes an alert
actionable instead of asking an LLM to decide whether protection exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Mapping

from scanner.execution.guards import RiskLimits
from scanner.execution.ownership import GEMMA, MOMENTUM
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
    owners = owner_map or {}
    exempt = {s for s, owner in owners.items() if owner == MOMENTUM}
    positions = tuple(p for p in positions if str(p.get("symbol") or "") not in exempt)
    orders = tuple(o for o in orders if str(o.get("symbol") or "") not in exempt)
    # Gemma's book has its own slot count (gemma_max_positions); it still needs
    # stops and intents, so it is checked for those, just not counted here.
    shared = [p for p in positions if owners.get(str(p.get("symbol") or "")) != GEMMA]

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

    if len(shared) > limits.max_positions:
        findings.append(Finding("critical", "position_limit", f"{len(shared)} positions exceed limit {limits.max_positions}"))

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


def activate_kill_switch(path: str | Path, reason: str) -> None:
    """Create the kill switch file with a reason, so every scheduled task sees it.

    The file's first line is the reason; subsequent lines are additional
    context. A human must remove it deliberately — nothing in this codebase
    removes it automatically, which is the point: an automated halt needs a
    human to decide the conditions have passed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    target.write_text(
        f"ACTIVATED {stamp}\nREASON: {reason}\n",
        encoding="utf-8",
    )


#: Consecutive rejected orders before the kill switch is auto-activated.
MAX_CONSECUTIVE_REJECTIONS = 5


def rejection_count_path(kill_switch_path: str | Path) -> Path:
    """Sidecar file that tracks consecutive broker rejections."""
    return Path(str(kill_switch_path) + ".rejections")


def read_consecutive_rejections(kill_switch_path: str | Path) -> int:
    """How many orders have been rejected in a row."""
    p = rejection_count_path(kill_switch_path)
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or "0")
    except (ValueError, OSError):
        return 0


def record_rejection(kill_switch_path: str | Path) -> int:
    """Increment the consecutive-rejection counter and return the new total."""
    count = read_consecutive_rejections(kill_switch_path) + 1
    rejection_count_path(kill_switch_path).write_text(
        str(count), encoding="utf-8"
    )
    return count


def reset_rejections(kill_switch_path: str | Path) -> None:
    """Clear the consecutive-rejection counter after a successful order."""
    p = rejection_count_path(kill_switch_path)
    if p.exists():
        p.unlink()


def should_auto_activate(
    kill_switch_path: str | Path,
    *,
    consecutive_rejections: int | None = None,
    model_env_complete: bool = True,
    broker_reachable: bool = True,
) -> tuple[bool, str]:
    """Decide whether the kill switch should be auto-activated.

    Returns (should_activate, reason). Conditions:
      - MAX_CONSECUTIVE_REJECTIONS broker rejections in a row
      - model environment incomplete (Ollama down or model missing)
      - broker unreachable
    """
    if consecutive_rejections is None:
        consecutive_rejections = read_consecutive_rejections(kill_switch_path)

    if consecutive_rejections >= MAX_CONSECUTIVE_REJECTIONS:
        return True, (
            f"{consecutive_rejections} consecutive order rejections — "
            f"suspending new entries"
        )
    if not model_env_complete:
        return True, "model environment incomplete — dedup/news gate degraded"
    if not broker_reachable:
        return True, "broker unreachable — cannot confirm fills or stops"
    return False, ""


def format_report(report: SafetyReport) -> str:
    if report.healthy:
        return "SAFETY OK"
    return "\n".join(
        f"{f.severity.upper()} {f.code}{(' [' + f.symbol + ']') if f.symbol else ''}: {f.message}"
        for f in report.findings
    )


# --- session start equity -----------------------------------------------------

SESSION_EQUITY_PATH = Path("data/session_equity.json")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def capture_session_equity(equity: float) -> None:
    """Store today's starting equity so the daily-loss guard can measure
    from a real baseline instead of the current (possibly already-drawn-down)
    equity.

    Idempotent: re-capturing the same day overwrites with the latest value.
    """
    data = {}
    if SESSION_EQUITY_PATH.exists():
        try:
            data = json.loads(SESSION_EQUITY_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    data[_today_iso()] = equity
    SESSION_EQUITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_EQUITY_PATH.write_text(
        json.dumps(data, indent=1), encoding="utf-8"
    )


def load_session_equity() -> float | None:
    """Today's captured starting equity, or None if not yet captured."""
    if not SESSION_EQUITY_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_EQUITY_PATH.read_text(encoding="utf-8"))
        return float(data.get(_today_iso(), 0)) or None
    except (ValueError, OSError, TypeError):
        return None
