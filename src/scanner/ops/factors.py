"""Four-factor attribution (Carhart 1997) of the competition books (operator 2026-10-22: "Yes").

Splits a book's daily excess return into what it earned from the MARKET, SIZE,
VALUE and MOMENTUM, and ALPHA -- the part none of them explains, i.e. skill.

  R - rf = alpha + b_mkt MKT + b_smb SMB + b_hml HML + b_mom MOM + noise

Factors from ETFs, daily (Kenneth French's official series arrive 1-2 months late):
  MKT = SPY - rf, SMB = IWM - SPY, HML = IWD - IWF, MOM = MTUM - SPY; rf from ^IRX.
Book returns from data/competition/snapshots.jsonl: the change in total P/L over
the money invested the day before (the competition's own measure).

Too few days make alpha noise: a reading needs MIN_DAYS for a first look and
RELIABLE_DAYS (about 6 months) to mean much. PURE.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from scanner.models import Frozen

FACTORS = ("MKT", "SMB", "HML", "MOM")
MIN_DAYS = 15
RELIABLE_DAYS = 120


class Fit(Frozen):
    days: int
    alpha_daily: float
    alpha_t: float
    betas: dict[str, float]
    beta_t: dict[str, float]
    r2: float

    @property
    def alpha_yearly(self) -> float:
        return self.alpha_daily * 252


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-Jordan with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-15:
            raise ValueError("singular design: factors move together or too few days")
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _inverse(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    cols = [_solve(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols(x: Sequence[Sequence[float]], y: Sequence[float]) -> tuple[list[float], list[float], float]:
    """Coefficients, their t-stats, R^2. x has no intercept column; one is added."""
    rows = [[1.0, *r] for r in x]
    k, n = len(rows[0]), len(rows)
    if n <= k:
        raise ValueError(f"need more than {k} days, have {n}")
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * yy for r, yy in zip(rows, y)) for i in range(k)]
    coef = _solve(xtx, xty)
    resid = [yy - sum(c * v for c, v in zip(coef, r)) for r, yy in zip(rows, y)]
    ssr = sum(e * e for e in resid)
    my = sum(y) / n
    sst = sum((yy - my) ** 2 for yy in y)
    s2 = ssr / (n - k)
    inv = _inverse(xtx)
    se = [math.sqrt(max(inv[i][i] * s2, 0.0)) for i in range(k)]
    tstats = [c / s if s else float("nan") for c, s in zip(coef, se)]
    return coef, tstats, (1 - ssr / sst) if sst else float("nan")


def book_returns(snaps: Sequence, book: str) -> dict[date, float]:
    """day -> return in % from the competition snapshots (change in P/L over yesterday's money invested)."""
    rows = sorted((s for s in snaps if s.book == book), key=lambda s: s.day)
    out: dict[date, float] = {}
    for prev, cur in zip(rows, rows[1:]):
        if prev.invested > 0:
            out[cur.day] = (cur.pl_total - prev.pl_total) / prev.invested * 100
    return out


def factor_returns(closes: Mapping[str, Mapping[date, float]], rf_yearly_pct: Mapping[date, float]) -> dict[date, dict[str, float]]:
    """ETF closes (SPY, IWM, IWD, IWF, MTUM) -> daily factor returns in %."""
    days = sorted(set.intersection(*(set(v) for v in closes.values())))
    out = {}
    for prev, cur in zip(days, days[1:]):
        r = {k: (closes[k][cur] / closes[k][prev] - 1) * 100 for k in closes}
        rf = rf_yearly_pct.get(prev, 0.0) / 252
        out[cur] = {"MKT": r["SPY"] - rf, "SMB": r["IWM"] - r["SPY"], "HML": r["IWD"] - r["IWF"],
                    "MOM": r["MTUM"] - r["SPY"], "RF": rf}
    return out


def attribute(book: Mapping[date, float], factors: Mapping[date, Mapping[str, float]]) -> Fit:
    days = sorted(d for d in book if d in factors)
    if len(days) < MIN_DAYS:
        raise ValueError(f"only {len(days)} trading days -- need {MIN_DAYS} for a first reading")
    y = [book[d] - factors[d]["RF"] for d in days]
    x = [[factors[d][f] for f in FACTORS] for d in days]
    coef, t, r2 = ols(x, y)
    return Fit(days=len(days), alpha_daily=coef[0], alpha_t=t[0], betas=dict(zip(FACTORS, coef[1:])),
               beta_t=dict(zip(FACTORS, t[1:])), r2=r2)
