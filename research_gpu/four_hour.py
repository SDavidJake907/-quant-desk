"""TRIALS #89 (scalper) and #90 (absorption) on 4-HOUR candles.

Operator, 2026-10-22: "try both test on a 4 hour candle.. scalper, and obsorption".
4-hour candles are session-aligned as TradingView builds them for US stocks:
9:30-13:30 and 13:30-16:00 (two a day).

RULES -- FIXED 2026-10-22 IN THE COMMIT THAT ADDS THE TRIALS ROWS, BEFORE THE FIRST RUN.
#89 SCALPER
  data       data/intraday_alpaca 15m (501 S&P, 2024-09 -> 2026-09) -> 4h
  names      BIG (signal_lab.BIGCAP) and FAST (monthly fastest 30, point in time)
  entry      #83's signal on 4h candles: close crosses above the 20-candle SMA;
             limit at that close, filled only if one of the next 2 candles trades
             below it
  exits      ORIGINAL: sell limit at entry + $0.01, no stop, out at the close of
             the 10th candle (5 days) if unfilled; and #84's 12 dollar pairs
             (target +0.25/0.5/1/2 %, stop -0.5/1/2 %), same 10-candle time stop;
             stop first within a candle; 0.02% slip on market exits; no other cost
             on limit fills (as #83/#84)
  SELECT     per group: the dollar pair with the highest t in year 1 (to
             2025-08-31, >= 100 trades); PASS: year 2 mean > 0 with t >= 3
#90 ABSORPTION
  data       data/research_1m (2025-10 -> 2026-10, 93 names) -> 4h candles, with
             footprint_test's bulk-volume estimate of buy/sell per minute
  rules      footprint_test's sweep and absorption tests on 4h candles (20-candle
             swing low, 2x volume; >= 60% selling in the bottom quarter, close in
             the top half); entry next open; stop under the wick; target 2R; out
             at the close of the 9th candle (about a week); 0.05% a side
  PASS       ABSORB > 0 with t >= 3 in both halves (split 2026-04-15), above FAIR
             random (3 random 4h entries a trade, same risk and exits) and above
             the plain sweep. Expected to have too few trades to pass.
READ-ONLY research.
"""

from __future__ import annotations

import asyncio
import random
import sys
from collections import defaultdict
from datetime import date
from itertools import product
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "research_gpu"))

import footprint_test as fp  # noqa: E402
import scalper_test as st  # noqa: E402
import signal_lab as lab  # noqa: E402
import sweep_slow as ss  # noqa: E402

from scanner.signals import indicators as ind  # noqa: E402

TARGETS, STOPS = (0.25, 0.5, 1.0, 2.0), (0.5, 1.0, 2.0)
HOLD_SCALP, HOLD_ABSORB, N = 10, 9, 20
SPLIT_15 = date(2025, 9, 1)
SPLIT_1M = date(2026, 4, 15)


def bucket(t) -> tuple:
    return (t.date(), 0 if t.hour * 60 + t.minute < 13 * 60 + 30 else 1)


def to_4h(rows):
    out: list = []
    for t, o, h, lo, c, v in rows:
        k = bucket(t)
        if out and out[-1][0] == k:
            _, (t0, o0, h0, l0, _, v0) = out[-1]
            out[-1] = (k, (t0, o0, max(h0, h), min(l0, lo), c, v0 + v))
        else:
            out.append((k, (t, o, h, lo, c, v)))
    return [x for _, x in out]


def scalp_trades(b, allowed, target_pct, stop_pct):
    t = [x[0] for x in b]
    o, h, lo, c = ([x[k] for x in b] for k in range(1, 5))
    sma = ind.sma(c, 20)
    out, i = [], 21
    while i < len(c) - 2:
        if not (allowed(t[i]) and sma[i - 1] is not None and c[i - 1] < sma[i - 1] and c[i] > sma[i]):
            i += 1
            continue
        entry = c[i]
        fill = next((j for j in (i + 1, i + 2) if j < len(c) and lo[j] < entry), None)
        if fill is None:
            i += 1
            continue
        tgt = entry + 0.01 if target_pct is None else entry * (1 + target_pct / 100)
        stp = None if stop_pct is None else entry * (1 - stop_pct / 100)
        exit_px, j = None, fill
        last = min(fill + HOLD_SCALP, len(c) - 1)
        while j <= last:
            if j > fill and stp is not None and lo[j] <= stp:
                exit_px = min(o[j], stp) * (1 - st.SLIP)
                break
            if j > fill and h[j] > tgt:
                exit_px = tgt
                break
            if j == last:
                exit_px = c[j] * (1 - st.SLIP)
                break
            j += 1
        out.append((t[j].date(), (exit_px / entry - 1) * 100))
        i = j + 1
    return out


def summ(rows):
    n, m, t, w = ss.stats(rows) if rows else (0, float("nan"), float("nan"), float("nan"))
    return n, m, t, w


async def main() -> int:
    # ---------------- #89 scalper on 4h ----------------
    s15 = {p.name.split("__")[0]: lab.load_15m(p) for p in sorted((ROOT / "data/intraday_alpaca").glob("*__15m__session.json"))}
    s4 = {s: to_4h(b) for s, b in s15.items()}
    months = {(d.year, d.month) for b in s15.values() for d in (x[0] for x in b)}
    fast = lab.fast_months({s: lab.daily(b) for s, b in s15.items()}, 30)
    p89 = 0
    for gname, uni in (("BIG", {m: set(lab.BIGCAP) for m in months}), ("FAST", fast)):
        names = set().union(*uni.values()) & set(s4)
        print(f"\n== #89 scalper, 4h, {gname} ({len(names)} names) ==")
        results = {}
        for label, tp, sp in [("original +1c, no stop", None, None)] + [
                (f"+${a * 20:.0f} / -${b * 20:.0f}", a, b) for a, b in product(TARGETS, STOPS)]:
            rows = []
            for s in names:
                rows += scalp_trades(s4[s], lambda d, s=s: s in uni.get((d.year, d.month), ()), tp, sp)
            A = summ([r for r in rows if r[0] < SPLIT_15])
            B = summ([r for r in rows if r[0] >= SPLIT_15])
            results[label] = (A, B, tp)
            print(f"   {label:22} | Y1 {A[0]:5} {A[1]:+.3f}% t {A[2]:5.1f} win {A[3]:3.0f}% | "
                  f"Y2 {B[0]:5} {B[1]:+.3f}% t {B[2]:5.1f} win {B[3]:3.0f}%", flush=True)
        pairs = [k for k, (A, _, tp) in results.items() if tp is not None and A[0] >= 100]
        if pairs:
            best = max(pairs, key=lambda k: results[k][0][2] if results[k][0][2] == results[k][0][2] else -99)
            B = results[best][1]
            ok = B[1] > 0 and B[2] >= 3
            p89 += ok
            print(f"   SELECTED on year 1: {best} -> year 2 {B[1]:+.3f}% t {B[2]:.1f}: {'PASS' if ok else 'FAIL'}")
    # ---------------- #90 absorption on 4h ----------------
    async with httpx.AsyncClient(timeout=120) as client:
        fastm = await st.fast_by_month(client)
        data = await st.candles(client, set(lab.BIGCAP) | set().union(*fastm.values()))
    p90 = 0
    for gname, ok_name in (("BIG", lambda s, d: s in lab.BIGCAP),
                           ("FAST", lambda s, d: s in fastm.get((d.year, d.month), ()))):
        res: dict[str, list] = defaultdict(list)
        for s, mins in sorted(data.items()):
            hours, inside = fp.hourly_with_footprint(mins)
            # regroup hourly candles (and their minutes) into 4h buckets
            four, four_in = [], []
            for hr, mi in zip(hours, inside):
                k = bucket(hr[0])
                if four and bucket(four[-1][0]) == k:
                    t0, o0, h0, l0, _, v0 = four[-1]
                    four[-1] = (t0, o0, max(h0, hr[2]), min(l0, hr[3]), hr[4], v0 + hr[5])
                    four_in[-1] = four_in[-1] + mi
                else:
                    four.append(hr)
                    four_in.append(list(mi))
            if len(four) < 60:
                continue
            t = [x[0] for x in four]
            o, h, lo, c, v = ([x[k] for x in four] for k in range(1, 6))
            atr = ind.atr(h, lo, c, 14)
            elig = [k for k in range(N + 1, len(c) - 1) if ok_name(s, t[k])]
            rnd = random.Random(f"4h{s}{gname}")
            busy = {"SWEEP": -1, "ABSORB": -1}
            for i in elig:
                swing, avg = min(lo[i - N:i]), sum(v[i - N:i]) / N
                if not (lo[i] < swing < c[i] and avg and v[i] >= 2 * avg):
                    continue
                e = i + 1
                stop = lo[i] - 0.1 * (atr[i] or 0.0)
                risk = o[e] - stop
                if risk <= 0:
                    continue
                for version in ("SWEEP", "ABSORB"):
                    if version == "ABSORB" and not fp.absorbed(four[i], four_in[i]):
                        continue
                    if e <= busy[version]:
                        continue
                    net, j = ss.trade(t, o, h, lo, c, e, stop, o[e] + 2 * risk, HOLD_ABSORB)
                    res[version].append((t[j].date(), net))
                    busy[version] = j
                    if version == "ABSORB":
                        for _ in range(3):
                            r = rnd.choice(elig) + 1
                            rs = o[r] * (1 - risk / o[e])
                            cn, cj = ss.trade(t, o, h, lo, c, r, rs, o[r] + 2 * (o[r] - rs), HOLD_ABSORB)
                            res["RANDOM"].append((t[cj].date(), cn))
        print(f"\n== #90 absorption, 4h, {gname} ==")
        st_ = {}
        for k in ("ABSORB", "SWEEP", "RANDOM"):
            a = summ([r for r in res.get(k, []) if r[0] < SPLIT_1M])
            b = summ([r for r in res.get(k, []) if r[0] >= SPLIT_1M])
            st_[k] = (a, b)
            print(f"   {k:7} | half 1: {a[0]:4} trades {a[1]:+.2f}% t {a[2]:4.1f} | half 2: {b[0]:4} trades {b[1]:+.2f}% t {b[2]:4.1f}")
        (aa, ab), (sa, sb), (ra, rb) = st_["ABSORB"], st_["SWEEP"], st_["RANDOM"]
        ok = (aa[1] > 0 and aa[2] >= 3 and ab[1] > 0 and ab[2] >= 3 and aa[1] > ra[1] and ab[1] > rb[1]
              and aa[1] > sa[1] and ab[1] > sb[1])
        p90 += ok
        print(f"   ABSORB: {'PASS' if ok else 'FAIL'}")
    print(f"\n#89 PASS: {p89} of 2    #90 PASS: {p90} of 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
