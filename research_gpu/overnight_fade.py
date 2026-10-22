"""TRIALS #101: fade a mega-cap's overnight overreaction (from a strategy text the operator shared, 2026-10-22).

The text: on an after-hours report or macro news, if a mega-cap drops more than 4-6% within the
first overnight hours (8-10 PM ET) on thin volume, buy the dip with a limit order; a software stop
2% below (stops are rejected overnight), target 1.5 x the risk. Long only (this desk does not short).

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROW, BEFORE THE FIRST RUN.
  data      24/5 30m candles (SIP 04:00-20:00 + BOATS 20:00-04:00), 2024-09-16 -> now
            (around_clock.load_24), the 20 largest companies (signal_lab.BIGCAP)
  trigger   Mon-Thu, a 30m candle starting 20:00-21:30 ET closes >= DROP below that day's
            16:00 regular close (the 15:30 candle's close); DROP in {4%, 6%}; once per night
  entry     the next candle's open; 0.10% a side for an overnight / extended fill
  exits     software stop 2% below the entry (checked on each candle's low; a gap fills at
            the open), target +3% (1.5 x the 2% risk), else out at the next day's 16:00 close
            (0.05% at the regular close)
  control   RANDOM nights: the same name on Mon-Thu nights WITHOUT a trigger, entered at the
            20:30 candle, same exits -- the cost and drift of simply trading overnight
  PASS      the 4% version: mean net a trade > 0, above the control, t >= 3 over trades.
            Expect few trades; a result on fewer than 30 is reported as "not enough events".
READ-ONLY research.
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import around_clock as ac  # noqa: E402
import signal_lab as lab  # noqa: E402

STOP, TARGET = 0.02, 0.03


def cost(t) -> float:
    return 0.0005 if t == time(15, 30) or (time(9, 30) <= t < time(16, 0)) else 0.0010


def trade(bars, k):
    """Enter at bars[k] open; exits as fixed. -> (net %, reason)."""
    entry, t_in = bars[k][1], bars[k][0]
    stop, target = entry * (1 - STOP), entry * (1 + TARGET)
    end_day = t_in.date() + timedelta(days=1)
    ci = cost(t_in.time())
    for j in range(k, len(bars)):
        t, o, h, lo, c, _ = bars[j]
        if lo <= stop:
            px = min(o, stop) if j > k else stop
            return ((px * (1 - cost(t.time()))) / (entry * (1 + ci)) - 1) * 100, "stop"
        if h >= target:
            px = max(o, target) if j > k else target
            return ((px * (1 - cost(t.time()))) / (entry * (1 + ci)) - 1) * 100, "target"
        if t.date() >= end_day and t.time() >= time(15, 30):
            return ((c * (1 - 0.0005)) / (entry * (1 + ci)) - 1) * 100, "next close"
    t, *_, c, _ = bars[-1]
    return ((c * (1 - 0.0005)) / (entry * (1 + ci)) - 1) * 100, "end of data"


def run(drop: float):
    events, control = [], []
    for s in lab.BIGCAP:
        bars = ac.load_24(s)
        if len(bars) < 500:
            continue
        by_day = defaultdict(list)
        for i, b in enumerate(bars):
            by_day[b[0].date()].append(i)
        rnd = random.Random(f"fade{s}")
        for d, idx in by_day.items():
            if d.weekday() > 3:
                continue
            close_i = next((i for i in idx if bars[i][0].time() == time(15, 30)), None)
            if close_i is None:
                continue
            ref = bars[close_i][4]
            night = [i for i in idx if time(20, 0) <= bars[i][0].time() <= time(21, 30)]
            hit = next((i for i in night if bars[i][4] <= ref * (1 - drop)), None)
            if hit is not None and hit + 1 < len(bars):
                net, why = trade(bars, hit + 1)
                events.append((s, d, net, why, (bars[hit][4] / ref - 1) * 100))
            elif night and rnd.random() < 0.25:
                k = next((i for i in night if bars[i][0].time() == time(20, 30)), None)
                if k is not None:
                    control.append(trade(bars, k)[0])
    return events, control


def tstat(xs):
    return statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(len(xs)) if len(xs) > 2 and statistics.stdev(xs) else float("nan")


def main() -> int:
    for drop in (0.04, 0.06):
        ev, ctl = run(drop)
        nets = [e[2] for e in ev]
        print(f"\n== overnight drop of {drop * 100:.0f}%+ by 8-10 PM ET, mega-caps ==")
        for s, d, net, why, mv in sorted(ev, key=lambda e: e[1]):
            print(f"   {d} {s:5} fell {mv:+.1f}% vs the close -> {net:+.2f}% ({why})")
        if nets:
            print(f"   {len(nets)} trades: mean {statistics.mean(nets):+.2f}%, win {sum(n > 0 for n in nets) / len(nets) * 100:.0f}%, t {tstat(nets):.1f}")
        else:
            print("   no trades")
        print(f"   control (ordinary nights, same exits): {len(ctl)} trades, mean {statistics.mean(ctl):+.2f}%, t {tstat(ctl):.1f}")
        if drop == 0.04:
            ok = len(nets) >= 30 and statistics.mean(nets) > 0 and statistics.mean(nets) > statistics.mean(ctl) and tstat(nets) >= 3
            print(f"   PASS (4%): {'PASS' if ok else ('not enough events' if len(nets) < 30 else 'FAIL')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
