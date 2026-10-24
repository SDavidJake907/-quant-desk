"""Crash and shock alerts (operator 2026-10-23: "find what exactly happened yesterday, find what we
can use for an alert for a crash"; Gemma agreed the same night).

THURSDAY 10-22: the S&P 500 lost $1.1T (-1.47%) while 338 of its 495 stocks ROSE -- the 10 biggest
AI names lost more than the whole index, money rotated into staples / utilities / health care.
Cause: Meta's report the evening before. At 04:00 ET premarket chips (SMH) were already -3.1% and
the Nasdaq -1.7%; the account, concentrated in chips/AI, lost ~$1,060 in the first hour; the desk
monitor's first alarm came at 14:15 ET. 2007-2024: after a 2%+ sector gap-down, selling AT the open
usually lost (chips recovered +0.5% by the close, +1.1% in 5 days) -- the damage is the overnight
gap, so the useful warnings come BEFORE it.

  bellwether   a bellwether reports after today's close (or before tomorrow's open) and Gemma holds
               2+ names in the AI-heavy sectors             -> warns the evening before
  theme_shock  outside regular hours or at the open: chips (SMH) <= -2% or the Nasdaq (QQQ) <= -1.5%
               vs the prior close while she holds that theme  -> warns before the open
  stress       SPY <= -1.5% or VIX >= 25
  rotation     SPY down but most S&P stocks UP: a concentrated theme is hit, the market is not
  crash        SPY <= -3% with 80%+ of stocks down, or VIX >= 30

Alerts WAKE Gemma and go to the logs -- never to the operator's phone (declined 10-23), and they
never sell anything by themselves. Gemma's choice: a bellwether or theme_shock alert lets her drop
a holding in the alerted theme inside her 3-day minimum hold. PURE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from scanner.models import Frozen

BELLWETHERS = ("META", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "AAPL", "AVGO", "TSLA")
THEME_SECTORS = ("Information Technology", "Communication Services", "Consumer Discretionary")
THEME_SMH_PCT = -2.0
THEME_QQQ_PCT = -1.5
STRESS_SPY_PCT = -1.5
STRESS_VIX = 25.0
CRASH_SPY_PCT = -3.0
CRASH_DOWN_SHARE = 0.8
CRASH_VIX = 30.0
ROTATION_SPY_PCT = -0.75
ROTATION_UP_SHARE = 0.55
NIGHT_BEFORE_MIN_NAMES = 2
#: The kinds that let Gemma drop an alerted-theme holding inside her minimum hold.
OVERRIDES_MIN_HOLD = ("bellwether", "theme_shock")


class Alert(Frozen):
    kind: str
    text: str


def theme_holdings(held: Iterable[str], sectors: Mapping[str, str]) -> list[str]:
    return sorted(s for s in held if sectors.get(s) in THEME_SECTORS)


def classify(*, spy: float | None, qqq: float | None, smh: float | None, vix: float | None,
             up_share: float | None, theme_held: Sequence[str], reporting: Sequence[str],
             regular: bool) -> list[Alert]:
    """Every alert that applies now. `spy` / `qqq` / `smh`: % vs the prior close (premarket price
    outside regular hours); `up_share`: share of S&P stocks up today (None when unknown);
    `reporting`: bellwethers reporting before the next regular open."""
    out: list[Alert] = []
    held = ", ".join(theme_held)
    if len(theme_held) >= NIGHT_BEFORE_MIN_NAMES and reporting:
        out.append(Alert(kind="bellwether", text=(
            f"EARNINGS RISK before the next open: {', '.join(reporting)} report; you hold {held} in the same "
            "AI-heavy sectors (10-22: Meta's report took chips -3% before the open)")))
    shock = [f"{n} {v:+.1f}%" for n, v, line in (("chips SMH", smh, THEME_SMH_PCT), ("Nasdaq QQQ", qqq, THEME_QQQ_PCT))
             if v is not None and v <= line]
    if shock and theme_held:
        out.append(Alert(kind="theme_shock", text=(
            f"THEME SHOCK {'at the open' if regular else 'before the open'}: {', '.join(shock)} vs the prior close; "
            f"you hold {held}")))
    crash_breadth = up_share is not None and up_share <= 1 - CRASH_DOWN_SHARE
    spy_txt = "n/a" if spy is None else f"{spy:+.1f}%"
    vix_txt = f", VIX {vix:.0f}" if vix else ""
    if (spy is not None and spy <= CRASH_SPY_PCT and (crash_breadth or up_share is None)) or (vix or 0) >= CRASH_VIX:
        down = f", {(1 - up_share) * 100:.0f}% of stocks down" if up_share is not None else ""
        out.append(Alert(kind="crash", text=f"BROAD CRASH: SPY {spy_txt}{down}{vix_txt}"))
    elif (spy is not None and spy <= STRESS_SPY_PCT) or (vix or 0) >= STRESS_VIX:
        out.append(Alert(kind="stress", text=f"MARKET STRESS: SPY {spy_txt}{vix_txt}"))
    if (spy is not None and spy <= ROTATION_SPY_PCT and up_share is not None and up_share >= ROTATION_UP_SHARE):
        out.append(Alert(kind="rotation", text=(
            f"ROTATION, not a crash: SPY {spy:+.1f}% but {up_share * 100:.0f}% of S&P stocks are UP -- a concentrated "
            "theme is being sold; the SPY core rides it out")))
    return out


def active(rows: Iterable[Mapping], today: date) -> list[Alert]:
    """Stored alerts still in force today (each has an 'until' ISO date)."""
    return [Alert(kind=str(r["kind"]), text=str(r["text"])) for r in rows
            if str(r.get("until", "")) >= today.isoformat()]


def block(alerts: Sequence[Alert]) -> str:
    """Her briefing block, or ''."""
    if not alerts:
        return ""
    lines = ["DESK ALERTS (information -- nothing is sold by them; you decide):", *(f"- {a.text}" for a in alerts)]
    if any(a.kind in OVERRIDES_MIN_HOLD for a in alerts):
        lines.append("- Because of this alert you MAY drop a holding in the alerted sectors today even inside its "
                     "3-day minimum hold (your choice, 10-23). History: selling AFTER a gap-down usually lost; "
                     "the alert is worth most BEFORE the open.")
    return "\n".join(lines)
