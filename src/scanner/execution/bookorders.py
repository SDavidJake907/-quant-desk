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
from typing import Any, Optional

from scanner.config import settings
import asyncio

from scanner.execution import extended, manual, picks
from scanner.execution.alpaca import (
    BrokerError, entry_payload, round_tick, stop_payload, take_profit_payload,
)
from scanner.execution.audit import append_event
from scanner.execution.flatten_run import close_when_released
from scanner.execution.ownership import prefix_for
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.sessions import REGULAR
from scanner.execution.sizing import Sizing, size_position
from scanner.features.bars import fetch_bars
from scanner.features.intraday import IntradayBars, fetch_intraday, max_range
from scanner.features.intraday_context import atr_pct
from scanner.features.profile import build, ProfileError
from scanner.features.structure import Swing, swings
from scanner.models import AuditEvent, Frozen, TargetTier, TradeIntent


class Bought(Frozen):
    symbol: str
    ok: bool
    message: str
    sizing: Sizing | None = None


def log(event: str, symbol: str, reason: str, policy: TradingPolicy, **kw) -> None:
    append_event(settings.audit_log_path, AuditEvent(
        event=event, at=datetime.now().astimezone(), symbol=symbol, reason=reason,
        stage="trade", policy=policy.fingerprint, build=build_id(), **kw))


def target_price(*, limit: float, stop: float, reward_risk: float,
                 ceiling: float | None = None) -> float | None:
    """`reward_risk` x the risk above the entry; 0 means no target at all."""
    if reward_risk > 0:
        return round_tick(limit + reward_risk * (limit - stop))
    if ceiling is None:
        return None   # Gemma chose "none": ride it, no ceiling target
    return round_tick(limit * ceiling)


def _swing_high_target(bars: Optional[IntradayBars], entry_price: float,
                       lookback: int = 60, k: int = 2) -> float:
    """Target at recent swing high. Uses the most recent confirmed swing high
    above entry price as the target. Falls back to entry_price if no valid
    swing high found."""
    if bars is None or len(bars.closes) < k + 1:
        return entry_price * 1.5  # fallback: 50% above entry
    from scanner.features.structure import swings, Swing
    highs = swings(bars.highs, bars.lows, k)
    recent_highs = [s for s in highs if s.kind == "high" and s.price > entry_price
                    and s.index >= len(bars.closes) - lookback]
    if not recent_highs:
        return entry_price * 1.5
    return round_tick(min(s.price for s in recent_highs))


def _value_area_high_target(bars: Optional[IntradayBars], entry_price: float,
                            lookback: int = 20) -> float:
    """Target at volume profile value area high (VAH). Uses the value area high
    from the volume profile of recent bars as the target price."""
    if bars is None or len(bars.closes) < lookback:
        return entry_price * 1.5
    from scanner.features.profile import build, ProfileError
    try:
        profile = build(bars, len(bars.closes) - lookback, len(bars.closes))
        return round_tick(profile.value_area_high)
    except ProfileError:
        return entry_price * 1.5


def _fib_extension_target(bars: Optional[IntradayBars], entry_price: float,
                          lookback: int = 60, k: int = 2) -> float:
    """Fibonacci 1.618 extension target. Uses swing high - swing low range
    multiplied by 1.618 and added to swing low."""
    if bars is None or len(bars.closes) < k + 1:
        return entry_price * 1.5
    from scanner.features.structure import swings, Swing
    highs = swings(bars.highs, bars.lows, k)
    high_points = [s for s in highs if s.kind == "high"
                   and s.index >= len(bars.closes) - lookback]
    lows = swings(bars.lows, bars.highs, k) if hasattr(bars, 'lows') else []
    recent_lows = [s for s in lows if s.kind == "low"
                   and s.index >= len(bars.closes) - lookback]
    if not recent_highs or not recent_lows:
        return entry_price * 1.5
    swing_high = max(s.price for s in highs if s.price > entry_price)
    swing_low = min(s.price for s in lows if s.price < entry_price)
    fib_extension = swing_low + (swing_high - swing_low) * 1.618
    return round_tick(fib_extension)


async def price_and_atr(symbol: str, client) -> tuple[float, float, Optional[IntradayBars]]:
    intraday = await fetch_intraday(symbol, client, minutes=30, range_=max_range(30))
    daily = await fetch_bars(symbol, client, range_="6mo")
    last = float(intraday.closes[-1]) if intraday.closes else float(daily.closes[-1])
    return last, atr_pct(daily) or 0.0, intraday


async def buy_with_stop(broker, client, symbol: str, *, strategy: str, sleeve: float,
                        room: float, cap_pct: float, policy: TradingPolicy,
                        reason: str, risk_pct: float | None = None,
                        atr_multiple: float | None = None,
                        reward_risk: float | None = None,
                        targets: tuple[TargetTier, ...] = (),
                        session: str = REGULAR,
                        # Gemma's math-based menu choices (phase 2):
                        stop_menu_choice: str | None = None,
                        target_menu_choice: str | None = None,
                        size_menu_choice: str | None = None) -> Bought:
    """Price, size, send and log one bracket buy. Never raises on a broker or
    pricing failure; the refusal is logged and returned. `atr_multiple` and
    `reward_risk` are Gemma's menu choices; left out, the picks defaults.

    When `targets` is non-empty (take-profit ladder), the order is submitted
    as separate legs: entry limit + one limit sell per tier + one stop sell.
    Each tier sells `pct_of_shares` of the position at its `target_price`.
    The remaining shares ride to the final target or the stop.

    Gemma's math-based menu choices (phase 2):
    - stop_menu_choice: "swing_low", "vol_adjusted", "trailing" (overrides atr_multiple)
    - target_menu_choice: "swing_high", "value_area", "fib_1.618" (overrides reward_risk)
    - size_menu_choice: "kelly_half", "vol_adjusted" (overrides risk_pct)
    """
    try:
        last, atr, bars = await price_and_atr(symbol, client)
        if session != REGULAR:
            # Outside regular hours: today's extended-session price, fresh or nothing.
            live = await extended.price(symbol, client, session)
            if live is None:
                raise ValueError(f"no fresh {session} price")
            last = live

        # Gemma's math-based stop menu choice overrides ATR multiple
        use_structure = stop_menu_choice == "swing_low"
        use_regime = stop_menu_choice == "vol_adjusted"
        use_trailing = stop_menu_choice == "trailing"

        limit, stop, _ = picks.levels(
            last=last, atr_pct=atr,
            atr_multiple=policy.picks_stop_atr_multiple if atr_multiple is None else atr_multiple,
            offset_pct=policy.entry_limit_offset_pct, ceiling=policy.picks_target_ceiling,
            bars=bars,
            stop_use_structure=use_structure,
            stop_regime_adjust=use_regime,
            stop_trail_pct=policy.stop_trail_pct if use_trailing else 0.0,
            stop_trail_lookback=policy.stop_trail_lookback,
            atr_history=None,
        )
        # reward_risk is None when the picks list calls without specifying
        # (picks always uses picks_target_ceiling as its ceiling target).
        # reward_risk == 0.0 means Gemma explicitly chose "none" (ride it).
        # reward_risk > 0 means Gemma chose 2R/3R/5R.
        if reward_risk is None:
            effective_rr = 0.0
            target_ceiling = policy.picks_target_ceiling
        else:
            effective_rr = reward_risk
            target_ceiling = None if effective_rr == 0.0 else policy.picks_target_ceiling
        target = target_price(limit=limit, stop=stop, reward_risk=effective_rr,
                              ceiling=target_ceiling)
        # Gemma's math-based target menu choice overrides R:R multiple
        if target_menu_choice == "swing_high":
            target = _swing_high_target(bars, entry_price=limit)
        elif target_menu_choice == "value_area":
            target = _value_area_high_target(bars, entry_price=limit)
        elif target_menu_choice == "fib_1.618":
            target = _fib_extension_target(bars, entry_price=limit)
        # Gemma's math-based size menu choice overrides risk_pct
        effective_risk_pct = risk_pct if risk_pct is not None else policy.risk_pct
        if size_menu_choice == "kelly_half":
            effective_risk_pct = effective_risk_pct / 2.0
        elif size_menu_choice == "vol_adjusted":
            # Size inversely proportional to volatility: higher ATR% = smaller size
            effective_risk_pct = effective_risk_pct * (2.0 / max(atr, 1.0))
            effective_risk_pct = max(0.5, min(effective_risk_pct, 5.0))
    except Exception as exc:                               # noqa: BLE001
        log(f"{strategy}_refused", symbol, f"cannot price: {exc}", policy)
        return Bought(symbol=symbol, ok=False, message=f"cannot price: {exc}")
    sizing = size_position(sleeve, limit, stop,
                           risk_pct=effective_risk_pct,
                           max_position_pct=cap_pct, buying_power=room)
    if not sizing.is_tradeable:
        log(f"{strategy}_refused", symbol, sizing.limited_by, policy)
        return Bought(symbol=symbol, ok=False, message=sizing.limited_by)
    now = datetime.now().astimezone()
    coid = f"{prefix_for(strategy)}{symbol}-{int(now.timestamp())}"
    intent_target = target  # default; overridden in each branch below
    intent_rr = 0.0

    if session != REGULAR:
        # Outside regular hours: plain limit entry only. No stop or bracket
        # legs are allowed (Alpaca rejects them). protect.py arms the stop at
        # the 05:33 AK open (WORKLIST §59).
        try:
            payload = extended.limit_payload(symbol, sizing.shares, "buy",
                                             limit, coid)
            order = await broker.submit(payload)
            order_id = str(order.get("id", ""))
        except BrokerError as exc:
            log("order_rejected", symbol, str(exc), policy,
                intent=TradeIntent(symbol=symbol, action="buy",
                                   strategy=strategy, trigger_at=now,
                                   entry_price=limit, stop_price=stop,
                                   target_price=target,
                                   shares=sizing.shares,
                                   risk_dollars=sizing.risk_dollars,
                                   position_dollars=sizing.position_dollars,
                                   reward_risk=0.0,
                                   paper=True, client_order_id=coid,
                                   targets=targets,
                                   llm_concern=reason[:300]))
            return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    elif targets:
        # Take-profit ladder: entry limit + per-tier limit sells + stop sell,
        # all as separate orders. Each tier sells pct_of_shares at target_price.
        # The stop protects whatever shares remain after all tiers fill.
        intent_target = 0.0
        intent_rr = 0.0
        try:
            # 1. Entry limit order (full shares)
            entry_order = await broker.submit(entry_payload(
                symbol, sizing.shares, limit, now, coid,
                entry_offset_pct=policy.entry_limit_offset_pct))
            order_id = str(entry_order.get("id", ""))

            # 2. Take-profit limit sells for each tier
            remaining_shares = sizing.shares
            for tier in targets:
                tier_shares = max(1, int(sizing.shares * tier.pct_of_shares))
                remaining_shares -= tier_shares
                await broker.submit(take_profit_payload(
                    symbol, tier_shares, tier.target_price, tier.label, now, coid))

            # 3. Stop loss for whatever remains after all tiers
            stop_shares = max(1, remaining_shares)
            await broker.submit(stop_payload(
                symbol, stop_shares, stop, now, coid))
        except BrokerError as exc:
            log("order_rejected", symbol, str(exc), policy,
                intent=TradeIntent(symbol=symbol, action="buy",
                                   strategy=strategy, trigger_at=now,
                                   entry_price=limit, stop_price=stop,
                                   target_price=intent_target,
                                   shares=sizing.shares,
                                   risk_dollars=sizing.risk_dollars,
                                   position_dollars=sizing.position_dollars,
                                   reward_risk=intent_rr,
                                   paper=True, client_order_id=coid,
                                   targets=targets,
                                   llm_concern=reason[:300]))
            return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    elif effective_rr > 0:
        # Gemma chose 2R/3R/5R: single bracket order
        order_target = target
        intent_target = target
        intent_rr = round((target - limit) / (limit - stop), 3)
        try:
            payload = manual.bracket(symbol, sizing.shares, limit=limit,
                                     stop=stop, target=order_target, at=now)
            payload["client_order_id"] = coid
            order = await broker.submit(payload)
            order_id = str(order.get("id", ""))
        except BrokerError as exc:
            log("order_rejected", symbol, str(exc), policy,
                intent=TradeIntent(symbol=symbol, action="buy",
                                   strategy=strategy, trigger_at=now,
                                   entry_price=limit, stop_price=stop,
                                   target_price=intent_target,
                                   shares=sizing.shares,
                                   risk_dollars=sizing.risk_dollars,
                                   position_dollars=sizing.position_dollars,
                                   reward_risk=intent_rr,
                                   paper=True, client_order_id=coid,
                                   llm_concern=reason[:300]))
            return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    elif target_ceiling is not None:
        # Picks list: ceiling target (2.0x entry), reward_risk not specified
        order_target = target  # which is limit * ceiling
        intent_target = target
        intent_rr = round((target - limit) / (limit - stop), 3)
        try:
            payload = manual.bracket(symbol, sizing.shares, limit=limit,
                                     stop=stop, target=order_target, at=now)
            payload["client_order_id"] = coid
            order = await broker.submit(payload)
            order_id = str(order.get("id", ""))
        except BrokerError as exc:
            log("order_rejected", symbol, str(exc), policy,
                intent=TradeIntent(symbol=symbol, action="buy",
                                   strategy=strategy, trigger_at=now,
                                   entry_price=limit, stop_price=stop,
                                   target_price=intent_target,
                                   shares=sizing.shares,
                                   risk_dollars=sizing.risk_dollars,
                                   position_dollars=sizing.position_dollars,
                                   reward_risk=intent_rr,
                                   paper=True, client_order_id=coid,
                                   llm_concern=reason[:300]))
            return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    else:
        # Gemma chose "none": effectively no target (ceiling at 1000x)
        order_target = limit * 1000.0
        intent_target = 0.0
        intent_rr = 0.0
        try:
            payload = manual.bracket(symbol, sizing.shares, limit=limit,
                                     stop=stop, target=order_target, at=now)
            payload["client_order_id"] = coid
            order = await broker.submit(payload)
            order_id = str(order.get("id", ""))
        except BrokerError as exc:
            log("order_rejected", symbol, str(exc), policy,
                intent=TradeIntent(symbol=symbol, action="buy",
                                   strategy=strategy, trigger_at=now,
                                   entry_price=limit, stop_price=stop,
                                   target_price=intent_target,
                                   shares=sizing.shares,
                                   risk_dollars=sizing.risk_dollars,
                                   position_dollars=sizing.position_dollars,
                                   reward_risk=intent_rr,
                                   paper=True, client_order_id=coid,
                                   llm_concern=reason[:300]))
            return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")

    intent = TradeIntent(
        symbol=symbol, action="buy", strategy=strategy, trigger_at=now,
        entry_price=limit, stop_price=stop, target_price=intent_target,
        shares=sizing.shares, risk_dollars=sizing.risk_dollars,
        position_dollars=sizing.position_dollars,
        reward_risk=intent_rr,
        paper=True, client_order_id=payload.get("client_order_id", coid) if not targets else coid,
        targets=targets,
        llm_concern=reason[:300])
    log("trade_intent", symbol, reason, policy, intent=intent)
    log("order_submitted", symbol, reason, policy, intent=intent,
        order_id=order_id)
    return Bought(symbol=symbol, ok=True, sizing=sizing,
                  message=(f"{sizing.shares} sh limit {limit:,.2f} stop {stop:,.2f} "
                           f"({(limit - stop) / limit * 100:.1f}% below), "
                           f"risk {sizing.risk_dollars:,.0f}, limited by {sizing.limited_by}"
                           + ("" if session == REGULAR
                              else f" [{session}: stop arms at the open]")))


async def sell(broker, symbol: str, open_orders, *, strategy: str, reason: str,
               policy: TradingPolicy, session: str = REGULAR, client=None) -> bool:
    """Cancel the symbol's working orders (its bracket legs), then close the
    position once the shares are released. Logged either way. Outside regular
    hours the close is a limit order a little under the live price (market
    orders are not accepted then)."""
    for order in open_orders:
        if order.get("symbol") == symbol:
            try:
                await broker.cancel_order(str(order["id"]))
            except BrokerError:
                pass
    if session != REGULAR:
        return await _sell_extended(broker, symbol, strategy=strategy, reason=reason,
                                    policy=policy, session=session, client=client)
    failed = await close_when_released(broker, (symbol,))
    ok = symbol not in failed
    log(f"{strategy}_exit", symbol,
        reason + ("; closed at market" if ok else f"; CLOSE FAILED: {failed.get(symbol)}"),
        policy)
    return ok


async def _sell_extended(broker, symbol: str, *, strategy: str, reason: str,
                         policy: TradingPolicy, session: str, client) -> bool:
    live = await extended.price(symbol, client, session) if client is not None else None
    qty = 0
    for _ in range(10):                              # wait for cancelled legs to release
        held = {p["symbol"]: p for p in await broker._get("/v2/positions")}
        if symbol not in held:
            log(f"{strategy}_exit", symbol, reason + "; already closed", policy)
            return True
        qty = int(float(held[symbol].get("qty_available") or 0))
        if qty > 0:
            break
        await asyncio.sleep(2)
    if live is None or qty < 1:
        why = "no fresh price" if live is None else "shares not released"
        log(f"{strategy}_exit", symbol, f"{reason}; {session} CLOSE FAILED: {why}", policy)
        return False
    limit = live * (1 - policy.extended_sell_offset_pct / 100)
    coid = f"{prefix_for(strategy)}{symbol}-x{int(datetime.now().timestamp())}"
    try:
        await broker.submit(extended.limit_payload(symbol, qty, "sell", limit, coid))
    except BrokerError as exc:
        log(f"{strategy}_exit", symbol, f"{reason}; {session} CLOSE FAILED: {exc}", policy)
        return False
    log(f"{strategy}_exit", symbol, f"{reason}; {session} limit sell {qty} sh at {limit:,.2f}", policy)
    return True
