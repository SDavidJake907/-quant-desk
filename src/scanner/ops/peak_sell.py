"""Peak-sell readings for Gemma (operator, 2026-10-22).

The operator: sell at the high and take the profit. The rule uses only the indicators -- no dates,
days of the week or stock names ("stocks prices change, like today its at the lowest"). The six
checks are the pattern indicators show at a high, each threshold set a little LOWER than the
highest readings so it triggers just before the top.

NOT a tested edge -- an operator rule, logged in FREEZE.md. The code computes every reading; Gemma
sees them and decides; she can only sell a position that is in profit and scores at least MIN_MET.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from scanner.signals import indicators as ind

PLAN_FILE = Path("data/gemma/peak_sell_prompt.md")
MIN_MET = 4                       # of the 6 checks
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 0)

# name -> (label shown to Gemma, threshold, the highest readings it sits under)
CHECKS: dict[str, tuple[str, float, str]] = {
    "trend_5m": ("5-minute SuperTrend is UP", 1, "up"),
    "vwap_5m": ("price is at least +0.20% above today's VWAP", 0.20, "+0.25% and higher"),
    "crsi_1h": ("1-hour Connors RSI >= 63", 63, "70 and higher"),
    "stc_1h": ("1-hour Schaff Trend Cycle >= 85", 85, "94 and higher"),
    "stc_1d": ("daily Schaff Trend Cycle >= 80", 80, "90 and higher"),
    "pctb_1d": ("daily Bollinger %B >= 0.80", 0.80, "0.88 and higher"),
}

Candle = tuple[datetime, float, float, float, float, float]    # exchange-time start, o, h, l, c, v


@dataclass(frozen=True)
class Readings:
    symbol: str
    pl_pct: float                   # unrealized P/L on the position, %
    values: Mapping[str, float | None]

    def met(self) -> dict[str, bool | None]:
        """None = the reading is missing (counts as not met)."""
        out: dict[str, bool | None] = {}
        for k, (_, thr, _) in CHECKS.items():
            v = self.values.get(k)
            out[k] = None if v is None else v >= thr
        return out

    def score(self) -> int:
        return sum(1 for m in self.met().values() if m)

    def may_sell(self) -> bool:
        return self.pl_pct > 0 and self.score() >= MIN_MET


def _last(x: Sequence[float | None]) -> float | None:
    return x[-1] if x and x[-1] is not None else None


def pct_b(c: Sequence[float], n: int = 20) -> float | None:
    if len(c) < n:
        return None
    w = c[-n:]
    m = sum(w) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return (c[-1] - (m - 2 * sd)) / (4 * sd) if sd else None


def session(candles: Sequence[Candle]) -> list[Candle]:
    return [x for x in candles if SESSION_OPEN <= x[0].time() < SESSION_CLOSE]


def hourly(candles: Sequence[Candle]) -> list[Candle]:
    """Session 5m candles -> hours aligned to the 9:30 open (9:30, 10:30, ...)."""
    out: list[tuple[tuple, Candle]] = []
    for t, o, h, lo, c, v in candles:
        key = (t.date(), (t.hour * 60 + t.minute - 570) // 60)
        if out and out[-1][0] == key:
            p = out[-1][1]
            out[-1] = (key, (p[0], p[1], max(p[2], h), min(p[3], lo), c, p[5] + v))
        else:
            out.append((key, (t, o, h, lo, c, v)))
    return [x for _, x in out]


def readings(symbol: str, pl_pct: float, five: Sequence[Candle], daily_closes: Sequence[float]) -> Readings:
    """five: completed regular-session 5m candles (a few weeks); daily_closes: daily closes with
    today's price as the last one."""
    vals: dict[str, float | None] = dict.fromkeys(CHECKS)
    if len(five) >= 60:
        o, h, lo, c = ([x[k] for x in five] for k in (1, 2, 3, 4))
        st = ind.supertrend(o, h, lo, c, 10, 3)
        vals["trend_5m"] = 1.0 if st[-1] == 1 else 0.0
        today = [x for x in five if x[0].date() == five[-1][0].date()]
        vol = sum(x[5] for x in today)
        if vol:
            vwap = sum((x[2] + x[3] + x[4]) / 3 * x[5] for x in today) / vol
            vals["vwap_5m"] = (c[-1] / vwap - 1) * 100
        hc = [x[4] for x in hourly(five)]
        vals["crsi_1h"] = _last(ind.connors_rsi(hc))
        vals["stc_1h"] = _last(ind.stc(hc))
    if len(daily_closes) >= 60:
        vals["stc_1d"] = _last(ind.stc(list(daily_closes)))
        vals["pctb_1d"] = pct_b(list(daily_closes))
    return Readings(symbol=symbol, pl_pct=pl_pct, values=vals)


def _fmt(k: str, v: float | None) -> str:
    if v is None:
        return "n/a"
    if k == "trend_5m":
        return "UP" if v >= 1 else "DOWN"
    if k == "vwap_5m":
        return f"{v:+.2f}%"
    if k == "pctb_1d":
        return f"{v:.2f}"
    return f"{v:.0f}"


def table(r: Readings) -> str:
    met = r.met()
    rows = [f"  - {CHECKS[k][0]}: now {_fmt(k, r.values.get(k))} -> "
            f"{'MET' if m else 'missing' if m is None else 'not met'}" for k, m in met.items()]
    return "\n".join((f"[{r.symbol}] P/L {r.pl_pct:+.2f}% | {r.score()} of {len(CHECKS)} checks met"
                      f" | {'ELIGIBLE to sell' if r.may_sell() else 'not eligible'}", *rows))


def plan_text() -> str:
    try:
        return PLAN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return f"Sell a position in profit when at least {MIN_MET} of the {len(CHECKS)} peak checks are met."


def prompt(rs: Sequence[Readings], *, market: str) -> str:
    return "\n".join((
        plan_text(),
        "",
        f"Market now: {market}",
        "",
        "LIVE READINGS for each position you hold (computed by the desk, not estimated):",
        *(table(r) for r in rs),
        "",
        "For EACH position answer:",
        "- action: sell or hold",
        "- why: one sentence that names the readings you used",
        "Only an ELIGIBLE position can be sold; a 'sell' on any other is ignored.",
    ))


def schema(symbols: Sequence[str]) -> dict:
    item = {"type": "object", "properties": {
        "symbol": {"type": "string", "enum": list(symbols)},
        "action": {"type": "string", "enum": ["sell", "hold"]},
        "why": {"type": "string"}}, "required": ["symbol", "action", "why"]}
    return {"type": "object", "properties": {"positions": {"type": "array", "items": item}},
            "required": ["positions"]}


def approved(answer: Mapping | None, rs: Sequence[Readings]) -> list[tuple[str, str]]:
    """(symbol, why) she may sell: she said sell AND the code agrees it is eligible."""
    eligible = {r.symbol for r in rs if r.may_sell()}
    out = []
    for p in (answer or {}).get("positions", []) or []:
        s = str(p.get("symbol", ""))
        if p.get("action") == "sell" and s in eligible:
            out.append((s, str(p.get("why", ""))[:200]))
    return out
