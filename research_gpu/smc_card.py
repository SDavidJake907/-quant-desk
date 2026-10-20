"""TRIALS #77: the operator's "Simple Strategy" card, step by step, 4H trend + 15m entry.

Operator, 2026-10-19: "you didnt try diffrent timeframe.. follow this with the
indicators". The card (an SMC / ICT recipe):
  1 trend on 4H: higher highs + higher lows -> only buy (lower/lower -> only sell)
  2 key levels on 4H: previous highs and lows
  3 drop to 15m, wait for price to reach the key level
  4 wait for confirmation: CHoCH / BOS
  5 enter on the pullback into the Fair Value Gap
  6 stop below the structure, target the next high (liquidity)
  7 risk 1-2% per trade, don't overtrade

Not the same as TRIALS #1 (sweep + CHoCH + BOS, intraday only, no higher
timeframe, no FVG entry). Every choice the card leaves open is fixed HERE,
before the first run:

DATA  data/intraday_alpaca/<SYM>__15m__session.json, 501 S&P names, 2024-09 ->
      2026-09. 4H bars are built from them: 09:30-13:30 and 13:30-16:00 ET.
SWINGS fractal k=2 (a high above the 2 bars each side), CONFIRMED only once the
      2 later bars have closed -- nothing is seen early.
1 TREND  latest two confirmed 4H swing highs rising AND latest two swing lows
      rising = up (mirror = down).
2 LEVEL  the latest confirmed 4H swing low (support for a long).
3 REACH  a 15m low within 0.3% of that level (from above) while the trend is up
      -> armed. Disarmed after 52 bars (2 days) or if the trend stops being up.
4 CHoCH  a 15m close above the latest confirmed 15m swing high made since the
      touch (or within 26 bars before it).
5 FVG    the newest bullish gap (high[k-2] < low[k]) between the touch and the
      CHoCH bar. None -> no trade. Limit buy at the gap's top, valid 26 bars;
      cancelled if the target trades first.
6 STOP   the lowest low since the touch. TARGET the latest confirmed 4H swing
      high above the entry. Risk under 0.2% of price -> skipped.
  Exit: stop or target intrabar; both in one bar -> the stop (conservative);
  gaps fill at the open; 260 bars (10 sessions) -> out at the close.
  Cost 0.05% per side, charged in R. One trade per symbol at a time.
SHORTS the mirror, on the "only sell" side; reported separately.
CONTROL each trade gets 5 random entries on the same stock (random bar, at the
  open), SAME % stop and % target, same time limit.

PASS (long side, the primary): mean R after costs > 0 with t >= 2, > 0 in both
years (split 2025-10-01), AND long minus control > 0 with t >= 2.
Shorts need the same to count.
"""

from __future__ import annotations

import json
import math
import random
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CACHE = Path("data/intraday_alpaca")
SPLIT = date(2025, 10, 1)
K = 2
ZONE = 0.003
ARM_BARS, CHOCH_LOOKBACK, ENTRY_BARS, MAX_HOLD = 52, 26, 26, 260
MIN_RISK = 0.002
COST = 0.0005
DRAWS = 5


def load(sym):
    rows = json.loads((CACHE / f"{sym}__15m__session.json").read_text())
    out = []
    for t, o, h, l, c, _v in rows:
        dt = datetime.fromtimestamp(t, ET)
        if time(9, 30) <= dt.time() < time(16, 0):
            out.append((dt, o, h, l, c))
    return out


def four_hour_key(dt):
    return (dt.date(), 0 if dt.time() < time(13, 30) else 1)


def swings(highs, lows, upto):
    """Confirmed swing highs/lows among bars [0, upto): (index, price) lists."""
    sh, sl = [], []
    for j in range(K, upto - K):
        h, l = highs[j], lows[j]
        if all(h > highs[j - i] for i in range(1, K + 1)) and all(h >= highs[j + i] for i in range(1, K + 1)):
            sh.append((j, h))
        if all(l < lows[j - i] for i in range(1, K + 1)) and all(l <= lows[j + i] for i in range(1, K + 1)):
            sl.append((j, l))
    return sh, sl


def simulate(bars):
    """Long-side trades on (dt, o, h, l, c) bars. Returns [(entry_dt, R, stop%, tgt%, fill_idx)]."""
    n = len(bars)
    H = [b[2] for b in bars]
    L = [b[3] for b in bars]
    # 4H bars and, for each 15m bar, how many 4H bars are COMPLETE before it
    keys, h4h, h4l, last_of = [], [], [], []
    for i, b in enumerate(bars):
        k = four_hour_key(b[0])
        if not keys or keys[-1] != k:
            keys.append(k); h4h.append(b[2]); h4l.append(b[3]); last_of.append(i)
        else:
            h4h[-1] = max(h4h[-1], b[2]); h4l[-1] = min(h4l[-1], b[3]); last_of[-1] = i
    done_before = [0] * n
    j = 0
    for i in range(n):
        while j < len(last_of) and last_of[j] < i:
            j += 1
        done_before[i] = j
    # incremental confirmed swings
    sh4 = sl4 = None
    cache4 = {}
    sh15, sl15 = [], []
    trades = []
    state, arm_i, level, pull_low = "idle", 0, 0.0, 0.0
    pend = None
    pos = None
    for i in range(n):
        dt, o, h, l, c = bars[i]
        m = done_before[i]
        if m not in cache4:
            cache4[m] = swings(h4h, h4l, m)
        sh4, sl4 = cache4[m]
        # 15m swings confirmed by bar i-1 (index i-1-K)
        jx = i - 1 - K
        if jx >= K:
            if all(H[jx] > H[jx - q] for q in range(1, K + 1)) and all(H[jx] >= H[jx + q] for q in range(1, K + 1)):
                sh15.append((jx, H[jx]))
            if all(L[jx] < L[jx - q] for q in range(1, K + 1)) and all(L[jx] <= L[jx + q] for q in range(1, K + 1)):
                sl15.append((jx, L[jx]))
        up = len(sh4) >= 2 and len(sl4) >= 2 and sh4[-1][1] > sh4[-2][1] and sl4[-1][1] > sl4[-2][1]

        if pos is not None:                         # manage the open trade
            entry, stop, tgt, fi = pos
            px = None
            if i > fi:
                if o <= stop: px = o
                elif l <= stop: px = stop
                elif o >= tgt: px = o
                elif h >= tgt: px = tgt
                elif i - fi >= MAX_HOLD: px = c
            if px is not None:
                risk = entry - stop
                trades.append((bars[fi][0], (px - entry) / risk - 2 * COST * abs(entry) / risk,
                               risk / entry, (tgt - entry) / entry, fi))
                pos = None
            continue
        if pend is not None:                        # waiting at the FVG
            entry, stop, tgt, until = pend
            if h >= tgt and l > entry:
                pend = None
            elif l <= entry:
                fill = min(o, entry)
                if fill <= stop:                    # gapped through the stop: in and out at the open
                    trades.append((dt, -2 * COST * abs(fill) / (entry - stop),
                                   (entry - stop) / entry, (tgt - entry) / entry, i))
                elif l <= stop:                       # filled and stopped in the same bar
                    risk = fill - stop
                    if risk > 0:
                        trades.append((dt, (stop - fill) / risk - 2 * COST * abs(fill) / risk,
                                       risk / fill, (tgt - fill) / fill, i))
                else:
                    pos = (fill, stop, tgt, i)
                pend = None
            elif i >= until:
                pend = None
            continue
        if state == "idle":
            if up and sl4 and i > 0:
                lvl = sl4[-1][1]
                if l <= lvl * (1 + ZONE) and bars[i - 1][4] > lvl * (1 + ZONE):
                    state, arm_i, level, pull_low = "armed", i, lvl, l
            continue
        # armed
        pull_low = min(pull_low, l)
        if not up or i - arm_i > ARM_BARS:
            state = "idle"
            continue
        recent = [s for s in sh15 if s[0] >= arm_i - CHOCH_LOOKBACK]
        if recent and c > recent[-1][1]:
            fvg = None
            for k in range(i, max(arm_i, 2) - 1, -1):
                if H[k - 2] < L[k]:
                    fvg = H[k - 2]
                    break
            state = "idle"
            if fvg is None:
                continue
            above = [p for _, p in sh4 if p > fvg]
            if not above:
                continue
            tgt = above[-1]
            stop = pull_low
            if not (stop < fvg < tgt) or (fvg - stop) / fvg < MIN_RISK:
                continue
            pend = (fvg, stop, tgt, i + ENTRY_BARS)
    return trades


def control(bars, trades, seed):
    """Random entries, same % stop / % target / time limit."""
    rng = random.Random(seed)
    out = []
    n = len(bars)
    for _dt, _r, sp, tp, _fi in trades:
        rs = []
        for _ in range(DRAWS):
            s = rng.randrange(1, n - 2)
            entry = bars[s][1]
            stop, tgt = entry * (1 - sp), entry * (1 + tp)
            px = None
            for i in range(s, min(n, s + MAX_HOLD + 1)):
                _, o, h, l, c = bars[i]
                if i > s and o <= stop: px = o
                elif l <= stop: px = stop
                elif i > s and o >= tgt: px = o
                elif h >= tgt: px = tgt
                elif i - s >= MAX_HOLD: px = c
                if px is not None:
                    break
            if px is None:
                px = bars[-1][4]
            risk = entry - stop
            rs.append((px - entry) / risk - 2 * COST * abs(entry) / risk)
        out.append(sum(rs) / len(rs))
    return out


def mirror(bars):
    return [(dt, -o, -l, -h, -c) for dt, o, h, l, c in bars]


def one(sym):
    bars = load(sym)
    if len(bars) < 2000:
        return sym, [], [], [], []
    lt = simulate(bars)
    mb = mirror(bars)
    st = simulate(mb)
    return (sym, [(t[0].date().isoformat(), t[1]) for t in lt], control(bars, lt, zlib.crc32(sym.encode())),
            [(t[0].date().isoformat(), t[1]) for t in st], control(mb, st, zlib.crc32(sym.encode()) + 1))


def stats(xs):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0, n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd else 0.0), n


def report(label, trades, ctrl):
    rs = [r for _, r in trades]
    y1 = [r for d, r in trades if date.fromisoformat(d) < SPLIT]
    y2 = [r for d, r in trades if date.fromisoformat(d) >= SPLIT]
    gap = [r - c for r, c in zip(rs, ctrl)]
    m, t, n = stats(rs)
    wins = sum(r > 0 for r in rs) / n if n else 0
    print(f"{label}: {n} trades, win rate {wins*100:.0f}%")
    print(f"   mean {m:+.3f}R  t {t:+.2f} | year 1 {stats(y1)[0]:+.3f}R (n {len(y1)}) | "
          f"year 2 {stats(y2)[0]:+.3f}R (n {len(y2)})")
    gm, gt, _ = stats(gap)
    print(f"   random control {stats(ctrl)[0]:+.3f}R | card minus control {gm:+.3f}R t {gt:+.2f}")
    ok = m > 0 and t >= 2 and stats(y1)[0] > 0 and stats(y2)[0] > 0 and gm > 0 and gt >= 2
    return ok


def main() -> int:
    syms = sorted(p.name.split("__")[0] for p in CACHE.glob("*__15m__session.json"))
    longs, lc, shorts, sc = [], [], [], []
    per = {}
    with ProcessPoolExecutor() as ex:
        for sym, lt, lcc, st, scc in ex.map(one, syms, chunksize=8):
            longs += lt; lc += lcc; shorts += st; sc += scc
            per[sym] = lt
    print(f"{len(syms)} stocks, 15m bars 2024-09 -> 2026-09, 4H built from them\n")
    ok_l = report("LONG (only buy in a 4H uptrend) -- PRIMARY", longs, lc)
    ok_s = report("SHORT (only sell in a 4H downtrend)", shorts, sc)
    print("\nher 7 (long side):")
    for s in ("COHR", "MPC", "MU", "NVDA", "SNDK", "VLO", "WDC"):
        rs = [r for _, r in per.get(s, [])]
        m, t, n = stats(rs)
        print(f"   {s:<5} {n:>3} trades, mean {m:+.3f}R, total {sum(rs):+.1f}R")
    print(f"\nPRIMARY (long): {'PASS' if ok_l else 'FAIL'}   short: {'PASS' if ok_s else 'fail'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
