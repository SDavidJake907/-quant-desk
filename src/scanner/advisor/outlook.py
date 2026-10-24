"""Gemma's 10-day outlooks: WHERE and HOW FAR (operator + Gemma 2026-10-25).

Operator: "the news tells us half the story, the indicators tell us where and how far." To test it on
live trades (no backtest), each outlook says, for one stock:
  p_up       her probability (0-100) that its close 10 trading days on is above the price now
  low/high   the range (% from the price now) she expects that close inside
  lean       news / indicators / balanced -- what her view rests on (her addition, to isolate
             "half the story"), and a one-sentence basis
Gemma's choices: horizon 10 trading days (the plan's time stop); an outlook for every NEW buy, and
once a week (her first regular-session decision of the week) for every holding she keeps; her
scorecard shown to her from MIN_SHOWN graded outlooks; outlooks NEVER change position size.

Graded after the 10th session's close: up or not (Brier score vs a coin flip, 0.25, and vs the base
rate seen afterwards), inside the range or not, and how wide the ranges were -- overall and by lean.
Separate from her 5-day "beat the S&P" probabilities (advisor/forecast_grade.py).
PURE: decisions and bars in, grades and text out.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from scanner.features.bars import Bars
from scanner.models import Frozen

HORIZON = 10
LEANS = ("news", "indicators", "balanced")
MIN_SHOWN = 20
COIN_BRIER = 0.25
#: The operator's goal (2026-10-25): "6 out of 8 predictions will be right" -- shown next to her hit rate.
TARGET_HIT = 0.75
P_FLOOR, P_CEIL = 0.01, 0.99
MAX_RANGE_PCT = 100.0

RULES = "\n".join((
    f"OUTLOOKS (where and how far; graded after {HORIZON} trading days, they never change size or trades):",
    "- In 'outlooks' give one for {who}.",
    f"- p_up: your probability, 0-100, that its close {HORIZON} trading days from now is ABOVE its price now.",
    f"- low_pct / high_pct: the range (% from the price now) you expect that close to land in. Size it from",
    f"  the stock's daily ATR in its facts: a {HORIZON}-day close usually lands within about 3 daily ATRs either",
    "  way (daily ATR 2.5% -> roughly -7.5% to +7.5%); shift it toward your view, e.g. -6 / +9.",
    "- lean: 'news' if your view rests mainly on the news, 'indicators' if mainly on the chart and numbers,",
    "  'balanced' if on both; basis: one sentence naming the news item and the indicator you used.",
    "- Be honest, not bold: 50 means no view, and a range that is too narrow will miss.",
))


def rules(holdings_due: bool) -> str:
    who = ("EVERY new buy AND every holding you keep (it is your weekly outlook on your book)"
           if holdings_due else "EVERY new buy (none if you buy nothing)")
    return RULES.replace("{who}", who)


def with_schema(answer_schema: Mapping[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
    """The decision schema plus the 'outlooks' array (possibly empty: no buys, not the weekly day)."""
    item = {"type": "object", "properties": {
        "symbol": {"type": "string", "enum": list(allowed)},
        "p_up": {"type": "number", "minimum": 0, "maximum": 100},
        "low_pct": {"type": "number"},
        "high_pct": {"type": "number"},
        "lean": {"type": "string", "enum": list(LEANS)},
        "basis": {"type": "string"}},
        "required": ["symbol", "p_up", "low_pct", "high_pct", "lean", "basis"]}
    return {**answer_schema,
            "properties": {**answer_schema["properties"], "outlooks": {"type": "array", "items": item}},
            "required": [*answer_schema["required"], "outlooks"]}


class Outlook(Frozen):
    day: str
    symbol: str
    kind: str              # buy | holding
    price: float
    p_up: float            # 0-1
    low_pct: float
    high_pct: float
    lean: str
    basis: str = ""


def parse(answer: Any, *, day: date, buys: Iterable[str], kept: Iterable[str], holdings_due: bool,
          prices: Mapping[str, float]) -> list[Outlook]:
    """Her outlooks for new buys (and, on the weekly day, kept holdings) that have a price now.
    The first per stock counts; p_up is clamped; a reversed range is put the right way round."""
    buys, kept = set(buys), set(kept)
    wanted = buys | (kept if holdings_due else set())
    items = answer.get("outlooks") if isinstance(answer, dict) else None
    out: dict[str, Outlook] = {}
    for o in items if isinstance(items, list) else ():
        if not isinstance(o, dict):
            continue
        s = str(o.get("symbol", "")).upper()
        if s not in wanted or s in out or not prices.get(s):
            continue
        try:
            p, lo, hi = float(o["p_up"]), float(o["low_pct"]), float(o["high_pct"])
        except (KeyError, TypeError, ValueError):
            continue
        if p != p or lo != lo or hi != hi:
            continue
        # She is asked for 0-100 but sometimes writes 0.65 (10-25 dry run read as 1%): 0-1 is a fraction.
        p = p if p <= 1 else p / 100
        lo, hi = sorted((max(-MAX_RANGE_PCT, lo), min(MAX_RANGE_PCT, hi)))
        out[s] = Outlook(day=day.isoformat(), symbol=s, kind="buy" if s in buys else "holding",
                         price=float(prices[s]), p_up=min(P_CEIL, max(P_FLOOR, p)), low_pct=lo,
                         high_pct=hi, lean=str(o.get("lean")) if o.get("lean") in LEANS else "balanced",
                         basis=str(o.get("basis", "")).strip()[:300])
    return list(out.values())


def _decisions(rows: Iterable[Mapping[str, Any]]):
    for r in rows:
        if r.get("event") != "gemma_decision":
            continue
        try:
            body = json.loads(r.get("reason") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(body, dict) and body.get("submitted") and body.get("outlooks"):
            yield body


def extract(rows: Iterable[Mapping[str, Any]]) -> list[Outlook]:
    """Every outlook from her submitted decisions; the first per stock and day counts."""
    seen, out = set(), []
    for body in _decisions(rows):
        for o in body["outlooks"]:
            try:
                ol = Outlook(**o)
            except Exception:                                 # noqa: BLE001 -- a bad row is skipped
                continue
            if (ol.day, ol.symbol) not in seen:
                seen.add((ol.day, ol.symbol))
                out.append(ol)
    return out


def holdings_due(made: Iterable[Outlook], today: date) -> bool:
    """No weekly holding outlook yet in today's ISO week."""
    week = today.isocalendar()[:2]
    return not any(o.kind == "holding" and date.fromisoformat(o.day).isocalendar()[:2] == week for o in made)


class Graded(Frozen):
    outlook: Outlook
    ret_pct: float

    @property
    def up(self) -> bool:
        return self.ret_pct > 0

    @property
    def inside(self) -> bool:
        return self.outlook.low_pct <= self.ret_pct <= self.outlook.high_pct


def realized(o: Outlook, bars: Bars) -> float | None:
    """% from her price to the close of the HORIZON-th session after the outlook's day, or None
    until it has closed."""
    after = [i for i, d in enumerate(bars.dates) if d.isoformat() > o.day]
    if len(after) < HORIZON or not o.price:
        return None
    return (bars.closes[after[HORIZON - 1]] / o.price - 1) * 100


def grade(made: Sequence[Outlook], bars: Mapping[str, Bars]) -> list[Graded]:
    out = []
    for o in made:
        b = bars.get(o.symbol)
        r = realized(o, b) if b is not None else None
        if r is not None:
            out.append(Graded(outlook=o, ret_pct=r))
    return out


def _part(gs: Sequence[Graded]) -> dict[str, Any]:
    n = len(gs)
    brier = sum((g.outlook.p_up - g.up) ** 2 for g in gs) / n
    base = sum(g.up for g in gs) / n
    called = [g for g in gs if g.outlook.p_up != 0.5]
    return {"n": n, "hit": sum((g.outlook.p_up > 0.5) == g.up for g in called) / len(called) if called else None, "brier": brier, "brier_base": sum((base - g.up) ** 2 for g in gs) / n,
            "up_share": base, "inside": sum(g.inside for g in gs) / n,
            "width": sum(g.outlook.high_pct - g.outlook.low_pct for g in gs) / n}


def score(graded: Sequence[Graded]) -> dict[str, Any]:
    if not graded:
        return {"n": 0}
    buckets = {}
    for lo, hi in ((0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 1.01)):
        inside = [g for g in graded if lo <= g.outlook.p_up < hi]
        if inside:
            buckets[f"{lo * 100:.0f}-{min(hi, 1.0) * 100:.0f}"] = {
                "n": len(inside), "said": sum(g.outlook.p_up for g in inside) / len(inside),
                "up": sum(g.up for g in inside) / len(inside)}
    return {**_part(graded), "coin": COIN_BRIER, "buckets": buckets,
            "by_lean": {k: _part(gs) for k in LEANS if (gs := [g for g in graded if g.outlook.lean == k])}}


def text(sc: Mapping[str, Any]) -> str:
    n = sc.get("n", 0)
    if n < MIN_SHOWN:
        return (f"Your {HORIZON}-day outlooks (where and how far): {n} graded so far -- your scorecard "
                f"is shown from {MIN_SHOWN}.")
    lines = [f"Your {HORIZON}-day outlooks (where and how far), {n} graded: Brier {sc['brier']:.3f} "
             f"(a coin flip scores {COIN_BRIER}; always saying the base rate of {sc['up_share'] * 100:.0f}% up "
             f"scored {sc['brier_base']:.3f}; lower is better). The close landed inside your range "
             f"{sc['inside'] * 100:.0f}% of the time; your ranges averaged {sc['width']:.1f} points wide."
             + (f" Direction right on {sc['hit'] * 100:.0f}% of your calls (the operator's goal: 6 of 8 = "
                f"{TARGET_HIT * 100:.0f}%)." if sc.get("hit") is not None else "")]
    for k, b in sc.get("buckets", {}).items():
        lines.append(f"- when you said {k}% (avg {b['said'] * 100:.0f}%): {b['up'] * 100:.0f}% were up ({b['n']})")
    for k, p in sc.get("by_lean", {}).items():
        lines.append(f"- leaning on {k}: {p['n']} graded, Brier {p['brier']:.3f}, inside the range "
                     f"{p['inside'] * 100:.0f}%")
    return "\n".join(lines)


def missing(made: Iterable[Outlook], wanted: Iterable[str]) -> list[str]:
    have = {o.symbol for o in made}
    return sorted(s for s in wanted if s not in have)


def followup_prompt(symbols: Sequence[str], facts: Mapping[str, str], reasons: Mapping[str, str],
                    holdings_due: bool) -> str:
    """When her decision left outlooks out (10-25 dry run: four buys, an empty list): one short ask for
    the missing ones, with each stock's facts and her own reason."""
    return "\n".join((
        "You are Gemma. Your decision just now did not include the outlooks the desk records. Write them now for: "
        + ", ".join(symbols) + ".", "",
        rules(holdings_due), "",
        *(f"[{s}] your reason: {reasons.get(s, '(kept holding)')}\n{facts.get(s, '')}\n" for s in symbols),
    ))


def followup_schema(symbols: Sequence[str]) -> dict[str, Any]:
    s = with_schema({"type": "object", "properties": {}, "required": []}, symbols)
    s["properties"]["outlooks"] = {**s["properties"]["outlooks"], "minItems": len(symbols)}
    return s
