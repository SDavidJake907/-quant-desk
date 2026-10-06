"""Chart-pattern detection, exactly as defined -- the single source of truth.

Lo, Mamaysky & Wang (2000), "Foundations of Technical Analysis", Journal of
Finance 55(4). FIXED 2026-10-06 before any data was examined:

  WINDOW     38 daily closes (35 for the pattern + 3 to confirm it), and
             nothing outside them: no lookahead.
  SMOOTHING  Nadaraya-Watson, Gaussian kernel, bandwidth 1.5 days, over the
             window only. (LMW chose bandwidth by cross-validation x 0.3; a
             fixed 1.5 is used so the definition cannot drift.)
  EXTREMA    where the smoothed slope changes sign; the extremum's price is
             the highest (peak) / lowest (trough) raw close within one day of
             it. Only extrema in the first 35 days count.
  E1..E5     the LAST five consecutive extrema (so a pattern is recent).

  HS    E1 peak; E3 > E1, E3 > E5; E1,E5 within 1.5% of their mean; E2,E4 within 1.5%
  IHS   E1 trough; E3 < E1, E3 < E5; same tolerances
  BTOP  E1 peak;   E1 < E3 < E5; E2 > E4             (broadening)
  BBOT  E1 trough; E1 > E3 > E5; E2 < E4
  TTOP  E1 peak;   E1 > E3 > E5; E2 < E4             (triangle)
  TBOT  E1 trough; E1 < E3 < E5; E2 > E4
  RTOP  E1 peak;   E1,E3,E5 within 0.75% of their mean; E2,E4 within 0.75%;
        lowest peak > highest trough                 (rectangle)
  RBOT  E1 trough; same, mirrored
  DTOP  E1 peak; Ea = highest later peak; E1,Ea within 1.5% of their mean;
        more than 22 days apart                      (double top)
  DBOT  E1 trough; Ea = lowest later trough; same    (double bottom)
First match in that order wins. The definitions overlap: a rectangle whose middle
peak is the highest also satisfies HS, and is reported as HS (LMW's own rules).

`card` turns a detection plus its MEASURED record (research_gpu/pattern_lab.py)
into one line of codes for Gemma. A pattern without a measured edge says so.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

WINDOW, PATTERN_DAYS, BANDWIDTH = 38, 35, 1.5
TOL, RECT_TOL, DOUBLE_GAP = 0.015, 0.0075, 22
CODES = ("HS", "IHS", "BTOP", "BBOT", "TTOP", "TBOT", "RTOP", "RBOT", "DTOP", "DBOT")

_WEIGHTS = [[math.exp(-0.5 * ((i - j) / BANDWIDTH) ** 2) for j in range(WINDOW)] for i in range(WINDOW)]
_WEIGHTS = [[w / sum(row) for w in row] for row in _WEIGHTS]


def smooth(prices: Sequence[float]) -> list[float]:
    return [sum(w * p for w, p in zip(row, prices)) for row in _WEIGHTS]


def extrema(prices: Sequence[float]) -> list[tuple[int, float, str]]:
    """(index, raw price, 'max'|'min') where the smoothed slope changes sign."""
    s = smooth(prices)
    out = []
    for k in range(1, PATTERN_DAYS - 1):
        before, after = s[k] - s[k - 1], s[k + 1] - s[k]
        near = prices[max(0, k - 1):k + 2]
        if before > 0 and after <= 0:
            out.append((k, max(near), "max"))
        elif before < 0 and after >= 0:
            out.append((k, min(near), "min"))
    return out


def _within(values: Sequence[float], tol: float) -> bool:
    m = sum(values) / len(values)
    return all(abs(v - m) <= tol * m for v in values)


def classify(ext: Sequence[tuple[int, float, str]]) -> str | None:
    if len(ext) < 5:
        return _double(ext)
    e = ext[-5:]
    (t1, e1, k1), (_, e2, _), (_, e3, _), (_, e4, _), (_, e5, _) = e
    peak = k1 == "max"
    if peak and e3 > e1 and e3 > e5 and _within((e1, e5), TOL) and _within((e2, e4), TOL):
        return "HS"
    if not peak and e3 < e1 and e3 < e5 and _within((e1, e5), TOL) and _within((e2, e4), TOL):
        return "IHS"
    if peak and e1 < e3 < e5 and e2 > e4:
        return "BTOP"
    if not peak and e1 > e3 > e5 and e2 < e4:
        return "BBOT"
    if peak and e1 > e3 > e5 and e2 < e4:
        return "TTOP"
    if not peak and e1 < e3 < e5 and e2 > e4:
        return "TBOT"
    tops, bots = ((e1, e3, e5), (e2, e4)) if peak else ((e2, e4), (e1, e3, e5))
    if _within(tops, RECT_TOL) and _within(bots, RECT_TOL) and min(tops) > max(bots):
        return "RTOP" if peak else "RBOT"
    return _double(e)


def _double(ext: Sequence[tuple[int, float, str]]) -> str | None:
    if len(ext) < 3:
        return None
    t1, e1, kind = ext[0]
    later = [(t, p) for t, p, k in ext[1:] if k == kind]
    if not later:
        return None
    ta, ea = max(later, key=lambda x: x[1]) if kind == "max" else min(later, key=lambda x: x[1])
    if _within((e1, ea), TOL) and ta - t1 > DOUBLE_GAP:
        return "DTOP" if kind == "max" else "DBOT"
    return None


def detect(prices: Sequence[float]) -> tuple[str | None, tuple]:
    """(pattern code or None, the extrema used) for exactly 38 closes."""
    if len(prices) != WINDOW:
        raise ValueError(f"need {WINDOW} closes, got {len(prices)}")
    ext = extrema(prices)
    code = classify(ext)
    return (code, tuple(ext[-5:])) if code else (None, ())


def card(*, code: str, age: int, neckline_atr: float, height_atr: float,
         stats: Mapping) -> str:
    """One line of codes: the pattern, where price is, and its MEASURED record."""
    return (f"PT={code} age{age} nk{neckline_atr:+.1f} h{height_atr:.1f} "
            f"EV20{stats['ev20']:+.2f}% t{stats['t']:.1f} n{stats['n']} {stats['verdict']}")
