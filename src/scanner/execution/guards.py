"""The checks that run before any order is sent.

An automated system trades whatever edge it has, repeatedly and without
hesitation — including a negative one. These guards do not make a strategy
profitable; they bound how wrong it can go before a human looks at it.

Every guard here is a refusal, never an adjustment. A blocked order is
reported and skipped, not quietly resized into something the caller did not
ask for.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from scanner.models import Frozen


class RiskLimits(Frozen):
    """Account-level bounds. Breach any and trading stops for the session."""

    #: Concurrent open positions. Correlation is not modelled, so five open
    #: longs in a falling market is one bet, not five.
    max_positions: int = 5

    #: Session loss, as a percentage of starting equity, after which the
    #: system stops opening trades. Existing brackets still run.
    max_daily_loss_pct: float = 3.0

    #: Below this equity the system refuses to trade at all.
    min_equity: float = 1_000.0

    #: Share of equity in any one name.
    max_position_pct: float = 20.0

    #: Share of equity a single losing trade may cost.
    risk_pct: float = 1.0


class Verdict(Frozen):
    """Whether an order may be sent, and why not."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Verdict(allowed=True)


def check_account(
    equity: float,
    starting_equity: float,
    open_positions: int,
    limits: RiskLimits,
    *,
    market_open: bool = True,
    account_status: str = "ACTIVE",
    account_blocked: bool = False,
) -> Verdict:
    """Account-level gate, evaluated once before a scan is acted on."""
    if not market_open:
        return Verdict(allowed=False, reason="market is closed")
    if account_status != "ACTIVE":
        return Verdict(allowed=False, reason=f"account status is {account_status}")
    if account_blocked:
        return Verdict(allowed=False, reason="account trading is blocked or suspended")
    if equity < limits.min_equity:
        return Verdict(
            allowed=False,
            reason=f"equity {equity:,.0f} below floor {limits.min_equity:,.0f}",
        )
    if open_positions >= limits.max_positions:
        return Verdict(
            allowed=False,
            reason=f"already holding {open_positions} of {limits.max_positions}",
        )
    if starting_equity > 0:
        drawdown = (starting_equity - equity) / starting_equity * 100.0
        if drawdown >= limits.max_daily_loss_pct:
            return Verdict(
                allowed=False,
                reason=f"down {drawdown:.2f}% today, limit {limits.max_daily_loss_pct}%",
            )
    return ALLOWED


def check_symbol(
    symbol: str,
    held: frozenset[str],
    pending: frozenset[str],
) -> Verdict:
    """Per-symbol gate.

    Refusing a symbol already held or already ordered is what stops a restart,
    a duplicate scan or a slow fill from opening the same trade twice — the
    failure that turns one position into three at the worst moment.
    """
    if symbol in held:
        return Verdict(allowed=False, reason="already holding")
    if symbol in pending:
        return Verdict(allowed=False, reason="order already working")
    return ALLOWED


def entry_drift_pct(entry: float, latest_close: float | None) -> float:
    """How far below the entry the latest completed close sits, in percent.

    Positive means price has fallen since the trigger. Zero for a setup whose
    trigger bar is the latest bar, because its close IS the entry.
    """
    if not latest_close or entry <= 0:
        return 0.0
    return (entry - latest_close) / entry * 100.0


def late_signal(entry: float, latest_close: float | None, max_pct: float) -> Verdict:
    """Refuse a setup whose price has already fallen `max_pct` below its entry.

    fresh_bars lets a trigger one bar old still be traded, at its OLD close as
    the limit. If the price has fallen since, that buy limit sits above the
    market and fills at once beside the stop -- APP (2026-08-20) and PSX
    (09-24) both filled ~1.1% under the limit, 0.5% from the stop, and were
    stopped within the hour. A price that rose instead is left alone: the limit
    then rests below the market and the stale-entry cancel removes it.
    """
    drift = entry_drift_pct(entry, latest_close)
    if drift >= max_pct:
        return Verdict(allowed=False,
                       reason=f"late signal: last close {drift:.2f}% below the entry "
                              f"(limit {max_pct:.2f}%) -- would fill beside the stop")
    return ALLOWED


def sector_concentration(
    held_symbols: Iterable[str],
    sector_of: dict[str, str],
    candidate_symbol: str,
    max_per_sector: int,
) -> Verdict:
    """Refuse a candidate if its sector already has max_per_sector positions.

    `max_per_sector <= 0` disables the check. A symbol whose sector is unknown
    is allowed through — an unknown sector is not a reason to block a trade,
    only a reason the cap can't be enforced for it.
    """
    if max_per_sector <= 0:
        return ALLOWED
    candidate_sector = sector_of.get(candidate_symbol, "")
    if not candidate_sector:
        return ALLOWED
    count = sum(1 for s in held_symbols if sector_of.get(s) == candidate_sector)
    if count >= max_per_sector:
        return Verdict(
            allowed=False,
            reason=f"sector cap: {count} open in {candidate_sector} (limit {max_per_sector})",
        )
    return ALLOWED
