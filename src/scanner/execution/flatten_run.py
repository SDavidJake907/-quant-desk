"""The one part of the Friday close-out that has to wait on the broker.

`flatten.py` decides; this acts. Kept apart so the planning module stays pure.

A close sent while a just-cancelled bracket leg still reserves the shares is
refused for insufficient quantity. So each symbol is closed only once the
broker reports its shares released, and a refusal costs a retry rather than
the weekend.

WAITING IS A COURTESY, NOT A VETO
If the shares never show as free -- a cancel that never lands, a position
record without `qty_available` -- the last attempt sends the close anyway.
Never trying is how a position stays open; trying and being refused at least
leaves the refusal in the log. And a failed READ of the book costs one retry,
never the whole job: an exception out of here would skip every close and the
audit record with it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from scanner.execution.alpaca import BrokerError
from scanner.execution.flatten import held_back

#: Seconds between reads of the book while waiting for cancels to land.
RELEASE_INTERVAL = 2.0

#: Reads before giving up. 30 x 2s is a minute, against fifteen before the
#: bell; cancels normally land in well under a second.
RELEASE_ATTEMPTS = 30

#: Hard ceiling however slow each broker call is, so the verify poll after
#: this and a hand close both still fit before the bell and inside the task's
#: 10-minute ExecutionTimeLimit.
RELEASE_DEADLINE_SECONDS = 240.0


def _open_symbols(positions) -> set[str]:
    held = set()
    for p in positions or []:
        try:
            if float(p.get("qty") or 0):
                held.add(str(p.get("symbol")))
        except (TypeError, ValueError):
            held.add(str(p.get("symbol")))   # unparseable is not proof of closed
    return held


async def _try_close(broker, symbol: str, errors: dict[str, str]) -> bool:
    try:
        await broker.close_position(symbol)
    except BrokerError as exc:
        errors[symbol] = str(exc)
        return False
    errors.pop(symbol, None)
    return True


async def close_when_released(
    broker,
    symbols: tuple[str, ...],
    *,
    attempts: int = RELEASE_ATTEMPTS,
    interval: float = RELEASE_INTERVAL,
    deadline_seconds: float = RELEASE_DEADLINE_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, str]:
    """Close each symbol once its shares are free. Returns {symbol: last error}
    for anything still not closed; empty means every close was accepted.
    Never raises on a broker error."""
    pending = list(symbols)
    errors: dict[str, str] = {}
    give_up_at = clock() + deadline_seconds
    for attempt in range(attempts):
        if attempt:
            await sleep(interval)
        last = attempt == attempts - 1 or clock() >= give_up_at
        try:
            positions = await broker._get("/v2/positions")
        except BrokerError as exc:
            for symbol in pending:
                errors[symbol] = f"reading positions: {exc}"
            if last:
                break
            continue
        # Already gone: closed by a stop, a target, or an earlier run.
        pending = [s for s in pending if s in _open_symbols(positions)]
        if not pending:
            return {}
        blocked = set() if last else set(held_back(positions, tuple(pending)))
        for symbol in [s for s in pending if s not in blocked]:
            if await _try_close(broker, symbol, errors):
                pending.remove(symbol)
        for symbol in blocked:
            errors[symbol] = "shares still held for orders"
        if not pending or last:
            break
    return {s: errors.get(s, "not closed") for s in pending}
