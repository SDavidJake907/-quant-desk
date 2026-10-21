"""Morning ideas: two more ways to find stocks, reported and graded (operator 2026-10-21).

Operator: "what other ways we can help find more stocks? beside yahoo?" -- "lets do all 3".
REPORT ONLY: nothing trades on either list, Gemma does not see them (the
quiet period until the Oct 31 verdict); the operator may use them for picks.

RULES, FIXED 2026-10-21 BEFORE THE FIRST LIST WAS MADE OR GRADED

EARNINGS WINNERS -- post-earnings drift (TRIALS #27; Ball & Brown 1968,
Bernard & Thomas 1989; weaker in large caps lately). The BEAT group of
scripts/pead_test.py, made live:
  universe  S&P 500 + MidCap 400 members today
  event     a Nasdaq earnings row with EPS and a forecast, listed on day d
  surprise  Nasdaq's surprise %, else (eps - forecast) / |forecast|; >= +5%
  EAR       close of d-1 -> close of d+1, stock minus the S&P 500; > 0
            (release time is unknown, so d+1 covers either timing)
  fresh     d+1 is the last completed session: listed the morning of d+2,
            the test's entry day
  ranked    by EAR; the first PICK_N are recorded

ESTIMATES RISING -- analysts raising this fiscal year's EPS estimate
(estimate-revision momentum; research-backed, untested here):
  universe  S&P 500 + MidCap 400 members today, Yahoo earningsTrend
  rule      estimate now vs 90 days ago >= +MIN_REVISION_PCT, at least
            MIN_UP analysts raising and none cutting in 30 days, and the price
            above its 50-day average
  ranked    by the 90-day estimate change; the first PICK_N are recorded

GRADING (both lists, as leaders_grade): equal-weight return from the open of
the list day to the close h-1 sessions later, minus the S&P 500 over the same
sessions, at h = 5, 21 and 60 (the PEAD test's hold). A day with an empty
list is skipped. Overlapping horizons overstate t: orientation only.

PURE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from scanner.models import Frozen
from scanner.ops.leaders_grade import list_return
from scanner.ops.selection import Bars, Summary, forward_return, summarize

LISTS = ("earnings", "estimates")
HORIZONS = (5, 21, 60)
PICK_N = 4
MIN_SURPRISE_PCT = 5.0
MIN_REVISION_PCT = 3.0
MIN_UP = 2


class EarningsPick(Frozen):
    symbol: str
    day: date
    surprise_pct: float
    ear_pct: float


class EstimatePick(Frozen):
    symbol: str
    revision_pct: float
    up_30d: int
    down_30d: int


def surprise(row: Mapping[str, str]) -> float | None:
    """Nasdaq's surprise %, else computed from EPS and the forecast."""
    try:
        if row.get("surprise_pct") not in (None, ""):
            return float(row["surprise_pct"])
        eps, fc = float(row["eps"]), float(row["eps_forecast"])
    except (KeyError, TypeError, ValueError):
        return None
    return (eps - fc) / abs(fc) * 100 if fc else None


def ear(stock: Sequence[tuple[date, float]], bench: Sequence[tuple[date, float]],
        day: date) -> tuple[float, date] | None:
    """(EAR %, d+1) from (day, close) series, or None if d-1 or d+1 is missing.
    d is the first session on or after `day`."""
    def window(series):
        days = [d for d, _ in series]
        i = next((k for k, d in enumerate(days) if d >= day), None)
        if i is None or i == 0 or i + 1 >= len(series):
            return None
        return series[i - 1][1], series[i + 1][1], series[i + 1][0]

    s, b = window(stock), window(bench)
    if s is None or b is None or s[2] != b[2] or not s[0] or not b[0]:
        return None
    return ((s[1] / s[0]) - (b[1] / b[0])) * 100, s[2]


def earnings_winners(events: Iterable[tuple[str, date, float]],
                     ears: Mapping[tuple[str, date], tuple[float, date]],
                     last_session: date) -> list[EarningsPick]:
    """Events (symbol, listed day, surprise %) that pass the BEAT rule and are
    fresh (d+1 == last_session), strongest EAR first; one per symbol."""
    out: dict[str, EarningsPick] = {}
    for symbol, day, sp in events:
        got = ears.get((symbol, day))
        if got is None or sp is None or sp < MIN_SURPRISE_PCT:
            continue
        e, d_plus_1 = got
        if e > 0 and d_plus_1 == last_session and symbol not in out:
            out[symbol] = EarningsPick(symbol=symbol, day=day, surprise_pct=sp, ear_pct=e)
    return sorted(out.values(), key=lambda p: -p.ear_pct)


def estimates_rising(funds: Mapping[str, object], above_50d: Mapping[str, bool]) -> list[EstimatePick]:
    """Fundamentals (with eps_now, eps_90d_ago, up_30d, down_30d) that pass the rule."""
    out = []
    for s, f in funds.items():
        now, ago = getattr(f, "eps_now", None), getattr(f, "eps_90d_ago", None)
        up, down = getattr(f, "up_30d", None) or 0, getattr(f, "down_30d", None) or 0
        if not now or not ago or ago <= 0 or not above_50d.get(s):
            continue
        rev = (now / ago - 1) * 100
        if rev >= MIN_REVISION_PCT and up >= MIN_UP and down == 0:
            out.append(EstimatePick(symbol=s, revision_pct=rev, up_30d=up, down_30d=down))
    return sorted(out, key=lambda p: (-p.revision_pct, p.symbol))


class Grade(Frozen):
    horizon: int
    excess: dict[str, Summary]


def grade(records: Sequence[Mapping], bars: Mapping[str, Bars], benchmark: Bars,
          horizon: int) -> Grade:
    excess: dict[str, list[float | None]] = {name: [] for name in LISTS}
    for rec in records:
        day = date.fromisoformat(rec["day"])
        bench = forward_return(benchmark, day, horizon)
        for name in LISTS:
            r = list_return(rec["lists"].get(name, []), bars, day, horizon)
            excess[name].append(r - bench if r is not None and bench is not None else None)
    return Grade(horizon=horizon, excess={n: summarize(v) for n, v in excess.items()})


def symbols_needed(records: Sequence[Mapping]) -> set[str]:
    return {s for rec in records for names in rec["lists"].values() for s in names}
