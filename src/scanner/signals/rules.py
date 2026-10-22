"""Buy/sell rules for each Pine signal, and one trade simulator (operator 2026-10-21).

Long only (the account trades stocks long). Each rule turns candles into a
STATE per bar: 1 = the signal says be long, 0 = be flat. A trade opens on the
bar AFTER the state flips to 1 (at that bar's open) and closes on the bar after
it flips back to 0 -- what a live order sent at the close of the signal bar
would get. Every trade also carries:
  - a protective stop STOP_ATR x ATR(22) under the entry (no martingale, no
    doubling down: MY_MACD_Strategy.pine's averaging-down is not used)
  - the weekend rule: flat at the close of the last bar on Friday
  - COST per side (spread + slippage on liquid fast movers)
The "+adx" variant of a rule takes an entry only when ADX(14) > ADX_MIN on the
signal bar (the operator's MyADX14-40.pine as a trend filter); exits unchanged.

PURE.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from scanner.models import Frozen
from scanner.signals import indicators as ind

STOP_ATR = 3.0
COST = 0.0005
ADX_MIN = 25.0


def _flag(cond: Sequence[bool]) -> list[int]:
    return [1 if x else 0 for x in cond]


def _machine(enter: Sequence[bool], leave: Sequence[bool]) -> list[int]:
    """1 from an entry bar until a leave bar (for oscillator rules)."""
    out, s = [], 0
    for e, x in zip(enter, leave):
        s = 1 if (s == 0 and e) else 0 if (s == 1 and x) else s
        out.append(s)
    return out


def _cross_up(x, level) -> list[bool]:
    return [False] + [x[i - 1] is not None and x[i] is not None and x[i - 1] <= level < x[i]
                      for i in range(1, len(x))]


def _cross_down(x, level) -> list[bool]:
    return [False] + [x[i - 1] is not None and x[i] is not None and x[i - 1] >= level > x[i]
                      for i in range(1, len(x))]


def _above(a, b) -> list[bool]:
    return [x is not None and y is not None and x > y for x, y in zip(a, b)]


def _stc(o, h, lo, c, v):
    s = ind.stc(c)
    return _machine(_cross_up(s, 25), _cross_down(s, 75))


def _connors(o, h, lo, c, v):
    r = ind.connors_rsi(c)
    return _machine([x is not None and x < 10 for x in r], [x is not None and x > 70 for x in r])


def _mfi(o, h, lo, c, v):
    m = ind.mfi(h, lo, c, v)
    return _machine(_cross_up(m, 20), _cross_up(m, 80))


def _vol_line(line):
    return _flag(_above(line, ind.sma(line, 21)))


#: name -> (what it is, rule). Fixed 2026-10-21 before the first test.
RULES: dict[str, tuple[str, Callable[..., list[int]]]] = {
    "supertrend": ("SuperTrend(22, 3) up", lambda o, h, lo, c, v: _flag([d == 1 for d in ind.supertrend(o, h, lo, c)])),
    "chandelier": ("Chandelier Exit(22, 3) long", lambda o, h, lo, c, v: _flag([d == 1 for d in ind.chandelier(h, lo, c)])),
    "halftrend": ("HalfTrend(2, 2) up", lambda o, h, lo, c, v: _flag([d == 1 for d in ind.halftrend(h, lo, c)])),
    "psar": ("Parabolic SAR under price", lambda o, h, lo, c, v: _flag(_above(c, ind.psar(h, lo)))),
    "nrtr": ("NRTR 2% up", lambda o, h, lo, c, v: _flag([d == 1 for d in ind.nrtr(c)])),
    "stc": ("Schaff Trend Cycle: in above 25, out under 75", _stc),
    "ema_cross": ("EMA 9 over EMA 21", lambda o, h, lo, c, v: _flag(_above(ind.ema(c, 9), ind.ema(c, 21)))),
    "connors": ("Connors RSI: in under 10, out over 70", _connors),
    "mfi": ("Money Flow: in up through 20, out up through 80", _mfi),
    "pvt": ("Price-Volume Trend over its 21-bar average", lambda o, h, lo, c, v: _vol_line(ind.pvt(c, v))),
    "obv": ("On-Balance Volume over its 21-bar average", lambda o, h, lo, c, v: _vol_line(ind.obv(c, v))),
}


class Trade(Frozen):
    symbol: str
    entry_at: datetime
    exit_at: datetime
    entry: float
    exit: float
    bars: int
    reason: str
    net_pct: float


def simulate(symbol: str, times: Sequence[datetime], o, h, lo, c, state: Sequence[int], *,
             entry_ok: Sequence[bool] | None = None, may_enter: Sequence[bool] | None = None,
             atr_series=None, stop_atr: float = STOP_ATR, cost: float = COST,
             close_open: bool = True) -> list[Trade]:
    """Trades from a state series. `may_enter[i]` gates entries (e.g. the name
    is in the fast universe that month); `entry_ok[i]` is the ADX filter. A
    position still open at the last bar is closed there when `close_open`."""
    a = atr_series if atr_series is not None else ind.atr(h, lo, c, 22)
    n = len(c)
    out: list[Trade] = []
    i = 1
    while i < n - 1:
        flip = state[i] == 1 and state[i - 1] != 1
        if not (flip and a[i] and (entry_ok is None or entry_ok[i])
                and (may_enter is None or may_enter[i])):
            i += 1
            continue
        j0 = i + 1
        entry, stop = o[j0], o[j0] - stop_atr * a[i]
        exit_px, reason, j = None, "", j0
        while j < n:
            if lo[j] <= stop:
                exit_px, reason = min(o[j], stop), "stop"
                break
            last_fri = times[j].weekday() == 4 and (j + 1 >= n or times[j + 1].date() != times[j].date())
            if last_fri:
                exit_px, reason = c[j], "friday close"
                break
            if state[j] == 0 and j + 1 < n:
                exit_px, reason, j = o[j + 1], "signal", j + 1
                break
            j += 1
        if exit_px is None:
            if not close_open:
                break
            j, exit_px, reason = n - 1, c[n - 1], "end of data"
        net = (exit_px * (1 - cost)) / (entry * (1 + cost)) - 1
        out.append(Trade(symbol=symbol, entry_at=times[j0], exit_at=times[j], entry=entry,
                         exit=exit_px, bars=j - j0 + 1, reason=reason, net_pct=net * 100))
        i = j
    return out
