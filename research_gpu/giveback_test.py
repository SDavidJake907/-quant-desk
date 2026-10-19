"""TRIALS #71: sell the whole book when it gives back part of its intraday gain.

Operator, 2026-10-19: the book was up $1,227 at 07:35 AK and $725 by 14:10 --
"find the TP where the highest bars were". A fixed TP from one day's high did not
carry over to the previous 20 sessions, so this tests the rule behind the idea:
once the day's book gain reaches A, sell everything if it gives back X of it.

Data: data/intraday_alpaca/<SYM>__15m__session.json (2024-09 -> 2026-09, regular
session, 15-minute bars). Pure stdlib.

Book: equal weight. Day return = from the previous session's last close.
Rule G(A, X): at each 15m bar close, gain = book return so far today. Once the
peak gain >= A and gain <= peak * (1 - X), sell all at that bar's close.
  rebuy "close": buy back at today's last close (keeps the overnight move)
  rebuy "open":  buy back at the next session's first open (misses that gap)
Cost: 0.05% per side on the whole book for each sell and each rebuy.
Hold: never sells, no cost.

Books
  random: 100 books of 7 S&P names, seed 71, fixed for the whole period
  hers:   COHR MPC MU NVDA SNDK VLO WDC (today's book; picked with hindsight)

PRIMARY: G(1%, 50%), rebuy close, random books. PASS = mean daily (rule - hold)
> 0 with t >= 2 over all days, AND > 0 in each year half (split at the middle
day). The other 11 grid cells are reported; one counts only at |t| >= 3.3.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE = Path("data/intraday_alpaca")
COST = 0.0005
ACTIVATIONS = (0.005, 0.01, 0.02)
GIVEBACKS = (0.25, 0.50)
REBUYS = ("close", "open")
PRIMARY = (0.01, 0.50, "close")
N_BOOKS, BOOK_SIZE, SEED = 100, 7, 71
HERS = ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC")


def load(sym: str) -> dict:
    """{date: [(open, close) per 15m bar]} for one symbol."""
    rows = json.loads((CACHE / f"{sym}__15m__session.json").read_text())
    days: dict = defaultdict(list)
    for t, o, _h, _l, c, _v in rows:
        days[datetime.fromtimestamp(t, ET).date()].append((o, c))
    return dict(days)


def book_days(data: dict, syms) -> list[dict]:
    """Per day with every symbol present: prev closes, opens, bar closes."""
    common = sorted(set.intersection(*(set(data[s]) for s in syms)))
    out = []
    for prev, day in zip(common, common[1:]):
        bars = [data[s][day] for s in syms]
        n = min(len(b) for b in bars)
        if n < 20:                      # half days and gaps: skip
            continue
        pc = [data[s][prev][-1][1] for s in syms]
        path = [sum(b[i][1] / p for b, p in zip(bars, pc)) / len(syms) - 1 for i in range(n)]
        out.append({"day": day, "path": path, "pc": pc, "syms": syms,
                    "open": [b[0][0] for b in bars], "close": [b[n - 1][1] for b in bars]})
    # next session's open per symbol, for the "open" rebuy
    for a, b in zip(out, out[1:]):
        a["next_open"] = b["open"]
        a["next_pc_gap"] = sum(o / c for o, c in zip(b["open"], a["close"])) / len(syms) - 1
    return out[:-1]


def rule_day(d: dict, act: float, give: float, rebuy: str) -> float:
    """Rule minus hold for one day (book returns, fractions)."""
    path = d["path"]
    hold = path[-1]
    peak = -1.0
    for g in path:
        peak = max(peak, g)
        if peak >= act and g <= peak * (1 - give):
            if rebuy == "close":
                return (g - COST) - COST - hold + 0.0   # out at g, back in at the close
            # out at g; back at next open: miss today's rest AND tonight's gap
            return (g - COST) - COST - hold - d["next_pc_gap"]
    return 0.0


def stats(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, (m / (sd / math.sqrt(n)) if sd else 0.0)


def run(books: list[list[dict]], act, give, rebuy) -> dict:
    by_day: dict = defaultdict(list)
    fired = total = 0
    for days in books:
        for d in days:
            r = rule_day(d, act, give, rebuy)
            by_day[d["day"]].append(r)
            total += 1
            fired += r != 0.0
    days = sorted(by_day)
    series = [sum(by_day[k]) / len(by_day[k]) for k in days]
    half = len(series) // 2
    m, t = stats(series)
    m1, t1 = stats(series[:half])
    m2, t2 = stats(series[half:])
    return {"n": len(series), "mean": m, "t": t, "h1": (m1, t1), "h2": (m2, t2),
            "fired": fired / total, "yearly": m * 252}


def main() -> int:
    syms = sorted(p.name.split("__")[0] for p in CACHE.glob("*__15m__session.json"))
    data = {s: load(s) for s in syms}
    rng = random.Random(SEED)
    random_books = [book_days(data, rng.sample(syms, BOOK_SIZE)) for _ in range(N_BOOKS)]
    hers = [book_days(data, HERS)]
    print(f"{len(syms)} symbols; random books {N_BOOKS} x {BOOK_SIZE}; "
          f"days per book ~{sum(map(len, random_books)) // N_BOOKS}; her book {len(hers[0])} days")
    hold_r = stats([sum(d["path"][-1] for d in b) / len(b) for b in random_books])
    print(f"hold, random books: mean daily {hold_r[0]*100:+.3f}%\n")
    print(f"{'A':>5} {'X':>4} {'rebuy':>5} | {'random: rule-hold/day':>22} {'t':>6} {'1st half':>9} "
          f"{'2nd half':>9} {'fires':>6} {'per yr':>7} | {'her book/day':>12} {'t':>6}")
    for act in ACTIVATIONS:
        for give in GIVEBACKS:
            for rebuy in REBUYS:
                r = run(random_books, act, give, rebuy)
                h = run(hers, act, give, rebuy)
                tag = "  <- PRIMARY" if (act, give, rebuy) == PRIMARY else ""
                print(f"{act*100:4.1f}% {give*100:3.0f}% {rebuy:>5} | {r['mean']*100:+21.3f}% "
                      f"{r['t']:+6.2f} {r['h1'][0]*100:+8.3f}% {r['h2'][0]*100:+8.3f}% "
                      f"{r['fired']*100:5.0f}% {r['yearly']*100:+6.1f}% | {h['mean']*100:+11.3f}% "
                      f"{h['t']:+6.2f}{tag}")
    p = run(random_books, *PRIMARY)
    ok = p["mean"] > 0 and p["t"] >= 2 and p["h1"][0] > 0 and p["h2"][0] > 0
    print(f"\nPRIMARY G(1%, 50%, rebuy close): {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
