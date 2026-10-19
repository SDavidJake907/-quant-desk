"""TRIALS #72: take profit at a PRICE LEVEL (where the highest bars were), not a %.

Operator, 2026-10-19: "find the TP where the highest bars were and use that for
the next trade" ... "test it with the price, and not % numbers".

Per stock, per day, starting the day held (as the books are):
  LEVEL rules -- a resting limit sell at a price from earlier bars:
    prev_high : yesterday's high
    high_5d   : the highest high of the last 5 sessions
  If today opens at/above the level, it fills at the open; else it fills at the
  level on the first 15m bar whose high exceeds it by a cent.
  DOLLAR rules -- sell when the position is up $D on the day (from yesterday's
  close), position size $3,500 (her average), limit at that price:
    $25, $50, $100
  Buy back at today's close (keeps the overnight move). Cost 0.05% per side.
  Hold: never sells.

Data: data/intraday_alpaca/<SYM>__15m__session.json, 501 S&P names, 2024-09 ->
2026-09. Days scored on rule minus hold, averaged over all stocks each day.

PRIMARY: prev_high, all 501 names. PASS = mean daily (rule - hold) > 0, t >= 2,
and > 0 in both halves. Other 4 rules count only at |t| >= 3.3. Her 7 names
(COHR MPC MU NVDA SNDK VLO WDC) are shown, not graded (picked with hindsight).
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE = Path("data/intraday_alpaca")
COST = 0.0005
TICK = 0.01
POSITION = 3500.0
DOLLARS = (25.0, 50.0, 100.0)
HERS = ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC")
PRIMARY = "prev_high"


def load(sym: str) -> list[tuple]:
    """[(date, [(o, h, l, c), ...]), ...] sorted, full sessions only."""
    rows = json.loads((CACHE / f"{sym}__15m__session.json").read_text())
    days: dict = defaultdict(list)
    for t, o, h, l, c, _v in rows:
        days[datetime.fromtimestamp(t, ET).date()].append((o, h, l, c))
    return [(d, b) for d, b in sorted(days.items()) if len(b) >= 20]


def fill(bars, level: float) -> float | None:
    """Limit-sell fill price at `level`, or None if never reached."""
    if bars[0][0] >= level:
        return bars[0][0]
    for _o, h, _l, _c in bars:
        if h >= level + TICK:
            return level
    return None


def day_diffs(days) -> dict[str, dict]:
    """{rule: {date: rule - hold}} for one stock."""
    out: dict = defaultdict(dict)
    for i in range(5, len(days)):
        day, bars = days[i]
        prev_close = days[i - 1][1][-1][3]
        close = bars[-1][3]
        levels = {
            "prev_high": max(b[1] for b in days[i - 1][1]),
            "high_5d": max(b[1] for _, bs in days[i - 5:i] for b in bs),
        }
        shares = POSITION / prev_close
        for d in DOLLARS:
            levels[f"${d:.0f}"] = prev_close + d / shares
        for rule, level in levels.items():
            px = fill(bars, level)
            out[rule][day] = 0.0 if px is None else (px - close) / prev_close - 2 * COST
    return out


def stats(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, m / (sd / math.sqrt(n))


def score(per_stock: list[dict], rule: str) -> dict:
    by_day: dict = defaultdict(list)
    fired = total = 0
    for s in per_stock:
        for day, r in s[rule].items():
            by_day[day].append(r)
            total += 1
            fired += r != 0.0
    series = [sum(v) / len(v) for _, v in sorted(by_day.items())]
    half = len(series) // 2
    m, t = stats(series)
    return {"mean": m, "t": t, "h1": stats(series[:half])[0], "h2": stats(series[half:])[0],
            "fired": fired / total, "n": len(series)}


def main() -> int:
    syms = sorted(p.name.split("__")[0] for p in CACHE.glob("*__15m__session.json"))
    per = {s: day_diffs(load(s)) for s in syms}
    rules = ["prev_high", "high_5d"] + [f"${d:.0f}" for d in DOLLARS]
    print(f"{len(syms)} stocks, ~{score(list(per.values()), PRIMARY)['n']} days\n")
    print(f"{'rule':<10} | {'all: rule-hold/day':>18} {'t':>6} {'1st half':>9} {'2nd half':>9} "
          f"{'fills':>6} {'per yr':>7} | {'her 7/day':>10} {'t':>6} {'fills':>6}")
    for rule in rules:
        a = score(list(per.values()), rule)
        h = score([per[s] for s in HERS if s in per], rule)
        tag = "  <- PRIMARY" if rule == PRIMARY else ""
        print(f"{rule:<10} | {a['mean']*100:+17.3f}% {a['t']:+6.2f} {a['h1']*100:+8.3f}% "
              f"{a['h2']*100:+8.3f}% {a['fired']*100:5.0f}% {a['mean']*252*100:+6.1f}% | "
              f"{h['mean']*100:+9.3f}% {h['t']:+6.2f} {h['fired']*100:5.0f}%{tag}")
    p = score(list(per.values()), PRIMARY)
    ok = p["mean"] > 0 and p["t"] >= 2 and p["h1"] > 0 and p["h2"] > 0
    print(f"\nPRIMARY prev_high: {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
