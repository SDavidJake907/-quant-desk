"""Live paper books for the Pine signals (operator 2026-10-21: "run it live/papermode").

One virtual book per VERSION ("15m supertrend+adx"), trading the day's fast
movers on live candles. VIRTUAL MONEY ONLY: nothing here sends an order, and
the Alpaca account (picks, Gemma) never sees it. Same trade rules as the lab
(rules.simulate), applied one completed candle at a time:
  - entry: the state flipped to 1 on the newest COMPLETED candle (and the ADX
    gate passed for "+adx") -> bought at the current price, which stands in for
    the next candle's open; a flip already acted on is never re-used
  - exit: the state is 0 on the newest completed candle -> sold at the current
    price; the 3 x ATR(22) stop if any candle since entry traded through it
    (filled at the stop); everything flat from 15:45 ET on Friday
  - NOTIONAL dollars a trade, COST a side
PURE: state in, state out; the script fetches candles and saves files.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from scanner.models import Frozen
from scanner.signals import rules

NOTIONAL = 1000.0


class Position(Frozen):
    symbol: str
    entry: float
    entry_at: datetime
    stop: float
    signal_bar: datetime


class Closed(Frozen):
    version: str
    symbol: str
    entry: float
    entry_at: datetime
    exit: float
    exit_at: datetime
    reason: str
    net_pct: float
    dollars: float


def _close(version: str, p: Position, price: float, at: datetime, reason: str) -> Closed:
    net = (price * (1 - rules.COST)) / (p.entry * (1 + rules.COST)) - 1
    return Closed(version=version, symbol=p.symbol, entry=p.entry, entry_at=p.entry_at, exit=price,
                  exit_at=at, reason=reason, net_pct=net * 100, dollars=net * NOTIONAL)


def step(version: str, symbol: str, *, times: Sequence[datetime], lows: Sequence[float],
         state: Sequence[int], gate_ok: bool, atr_now: float | None, price: float, now: datetime,
         position: Position | None, last_seen: datetime | None, friday_flat: bool
         ) -> tuple[Position | None, Closed | None, datetime | None]:
    """One symbol, one version, one run -> (position, closed trade, last bar acted on).
    `times/lows/state` hold COMPLETED candles only; `price` is the live price."""
    if not times:
        return position, None, last_seen
    newest = times[-1]
    if position is not None:
        since = [lo for t, lo in zip(times, lows) if t > position.signal_bar]
        if since and min(since) <= position.stop:
            return None, _close(version, position, min(position.stop, price), now, "stop"), newest
        if friday_flat:
            return None, _close(version, position, price, now, "friday close"), newest
        if state[-1] == 0 and (last_seen is None or newest > last_seen):
            return None, _close(version, position, price, now, "signal"), newest
        return position, None, newest
    fresh = last_seen is None or newest > last_seen
    flip = len(state) > 1 and state[-1] == 1 and state[-2] != 1
    if fresh and flip and gate_ok and atr_now and not friday_flat:
        pos = Position(symbol=symbol, entry=price, entry_at=now,
                       stop=price - rules.STOP_ATR * atr_now, signal_bar=newest)
        return pos, None, newest
    return None, None, newest


class Score(Frozen):
    version: str
    trades: int
    wins: int
    net_pct: float
    dollars: float


def leaderboard(closed: Sequence[Closed], open_marks: Mapping[str, float] | None = None) -> list[Score]:
    """Per version: closed trades, wins, summed net %, dollars; best first."""
    acc: dict[str, list[float]] = {}
    for t in closed:
        a = acc.setdefault(t.version, [0, 0, 0.0, 0.0])
        a[0] += 1
        a[1] += t.net_pct > 0
        a[2] += t.net_pct
        a[3] += t.dollars
    out = [Score(version=k, trades=int(v[0]), wins=int(v[1]), net_pct=v[2],
                 dollars=v[3] + (open_marks or {}).get(k, 0.0)) for k, v in acc.items()]
    return sorted(out, key=lambda s: -s.dollars)
