"""Buy-with-a-safety-stop and sell, shared by every list-driven book.

The picks list (scripts/picks_sync.py) and Gemma's book (scripts/gemma_trade.py)
place the same kind of order: a limit just above the last price, a stop
`atr_multiple` daily ATRs below, a target ceiling, a GTC bracket. Kept in one
place so the two books cannot drift into different order logic.

Every order is logged as a TradeIntent + order_submitted, which is what
protect.py reads to re-arm a lost stop and what the scorecard grades.
"""

from __future__ import annotations

from datetime import datetime

from scanner.config import settings
from scanner.execution import manual, picks
from scanner.execution.alpaca import BrokerError, round_tick
from scanner.execution.audit import append_event
from scanner.execution.flatten_run import close_when_released
from scanner.execution.ownership import prefix_for
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.sizing import Sizing, size_position
from scanner.features.bars import fetch_bars
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import atr_pct
from scanner.models import AuditEvent, Frozen, TradeIntent


class Bought(Frozen):
    symbol: str
    ok: bool
    message: str
    sizing: Sizing | None = None


def log(event: str, symbol: str, reason: str, policy: TradingPolicy, **kw) -> None:
    append_event(settings.audit_log_path, AuditEvent(
        event=event, at=datetime.now().astimezone(), symbol=symbol, reason=reason,
        stage="trade", policy=policy.fingerprint, build=build_id(), **kw))


def target_price(*, limit: float, stop: float, reward_risk: float, ceiling: float) -> float:
    """`reward_risk` x the risk above the entry; 0 means no target (the ceiling)."""
    if reward_risk > 0:
        return round_tick(limit + reward_risk * (limit - stop))
    return round_tick(limit * ceiling)


async def price_and_atr(symbol: str, client) -> tuple[float, float]:
    intraday = await fetch_intraday(symbol, client, minutes=30, range_=max_range(30))
    daily = await fetch_bars(symbol, client, range_="6mo")
    last = float(intraday.closes[-1]) if intraday.closes else float(daily.closes[-1])
    return last, atr_pct(daily) or 0.0


async def buy_with_stop(broker, client, symbol: str, *, strategy: str, sleeve: float,
                        room: float, cap_pct: float, policy: TradingPolicy,
                        reason: str, risk_pct: float | None = None,
                        atr_multiple: float | None = None,
                        reward_risk: float = 0.0) -> Bought:
    """Price, size, send and log one bracket buy. Never raises on a broker or
    pricing failure; the refusal is logged and returned. `atr_multiple` and
    `reward_risk` are Gemma's menu choices; left out, the picks defaults."""
    try:
        last, atr = await price_and_atr(symbol, client)
        limit, stop, _ = picks.levels(
            last=last, atr_pct=atr,
            atr_multiple=policy.picks_stop_atr_multiple if atr_multiple is None else atr_multiple,
            offset_pct=policy.entry_limit_offset_pct, ceiling=policy.picks_target_ceiling)
        target = target_price(limit=limit, stop=stop, reward_risk=reward_risk,
                              ceiling=policy.picks_target_ceiling)
    except Exception as exc:                               # noqa: BLE001
        log(f"{strategy}_refused", symbol, f"cannot price: {exc}", policy)
        return Bought(symbol=symbol, ok=False, message=f"cannot price: {exc}")
    sizing = size_position(sleeve, limit, stop,
                           risk_pct=policy.risk_pct if risk_pct is None else risk_pct,
                           max_position_pct=cap_pct, buying_power=room)
    if not sizing.is_tradeable:
        log(f"{strategy}_refused", symbol, sizing.limited_by, policy)
        return Bought(symbol=symbol, ok=False, message=sizing.limited_by)
    now = datetime.now().astimezone()
    payload = manual.bracket(symbol, sizing.shares, limit=limit, stop=stop,
                             target=target, at=now)
    payload["client_order_id"] = f"{prefix_for(strategy)}{symbol}-{int(now.timestamp())}"
    intent = TradeIntent(
        symbol=symbol, action="buy", strategy=strategy, trigger_at=now,
        entry_price=limit, stop_price=stop, target_price=target,
        shares=sizing.shares, risk_dollars=sizing.risk_dollars,
        position_dollars=sizing.position_dollars,
        reward_risk=round((target - limit) / (limit - stop), 3),
        paper=True, client_order_id=payload["client_order_id"], llm_concern=reason[:300])
    log("trade_intent", symbol, reason, policy, intent=intent)
    try:
        order = await broker.submit(payload)
    except BrokerError as exc:
        log("order_rejected", symbol, str(exc), policy, intent=intent)
        return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    log("order_submitted", symbol, reason, policy, intent=intent,
        order_id=str(order.get("id", "")))
    return Bought(symbol=symbol, ok=True, sizing=sizing,
                  message=(f"{sizing.shares} sh limit {limit:,.2f} stop {stop:,.2f} "
                           f"({(limit - stop) / limit * 100:.1f}% below), "
                           f"risk {sizing.risk_dollars:,.0f}, limited by {sizing.limited_by}"))


async def sell(broker, symbol: str, open_orders, *, strategy: str, reason: str,
               policy: TradingPolicy) -> bool:
    """Cancel the symbol's working orders (its bracket legs), then close the
    position once the shares are released. Logged either way."""
    for order in open_orders:
        if order.get("symbol") == symbol:
            try:
                await broker.cancel_order(str(order["id"]))
            except BrokerError:
                pass
    failed = await close_when_released(broker, (symbol,))
    ok = symbol not in failed
    log(f"{strategy}_exit", symbol,
        reason + ("; closed at market" if ok else f"; CLOSE FAILED: {failed.get(symbol)}"),
        policy)
    return ok
