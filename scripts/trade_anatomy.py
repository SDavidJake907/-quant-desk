"""Anatomy of the winners: every live trade, then winners vs losers in the backtest.

    python scripts/trade_anatomy.py

READ-ONLY research. Changes nothing live.

PART 1 -- LIVE. Every closed trade in the audit log, rebuilt from its own
order record (the levels actually sent) and the Yahoo 30m store, with what the
setup, the stock, its sector and the market looked like at the trigger, and
how the trade travelled: bars held, best point (MFE) and worst (MAE) in R.

PART 2 -- BACKTEST. The same features for every Mon-Thu setup in the 2y Alpaca
store, outcome with the Friday flatten (WORKLIST §29). Winners (R > 0) against
losers, split by time. A difference is marked STABLE only when it points the
same way in both halves -- with a dozen features compared, expect one or two
to line up by chance; this generates hypotheses, it does not confirm them.

POINT-IN-TIME throughout: 30m bars to the trigger, daily closes before its date.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import crosses_session
from scanner.features import history, technicals
from scanner.features.bars import fetch_bars, fetch_many
from scanner.features.intraday import SESSION_ALIGNED
from scanner.features.intraday_context import compute as context_of
from scanner.screens.smc import scan

from gemma_graded import daily_before
from sector_leadership_test import SectorMedians, sectors

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

V = TradingPolicy()
NUMERIC = ("stop %", "reward:risk", "rsi14", "volume ratio", "atr %",
           "vs ema200 30m %", "stock RS pp", "vs 200-day %", "sector RS pp",
           "S&P 1-mo %", "hour ET")
CATEGORIC = ("slot", "weekday", "market", "sector band")


def slot_of(t) -> str:
    return "open" if t.hour < 11 else "close" if t.hour >= 14 else "mid"


def travel(bars, i, entry, stop, target):
    """(R, reason, bars held, MFE R, MAE R) with the Friday flatten."""
    risk = entry - stop
    last = min(i + V.max_hold_bars, len(bars) - 1)
    hi, lo = entry, entry
    for j in range(i + 1, last + 1):
        o, h, l = bars.opens[j], bars.highs[j], bars.lows[j]
        hi, lo = max(hi, h), min(lo, l)
        done = None
        if o <= stop or o >= target:
            done = (o, "stop" if o <= stop else "target")
        elif l <= stop:
            done = (stop, "stop")
        elif h >= target:
            done = (target, "target")
        elif bars.times[j].weekday() == 4 and (j == last or bars.times[j + 1].date() != bars.times[j].date()):
            done = (bars.closes[j], "flattened")
        if done:
            return (done[0] - entry) / risk, done[1], j - i, (hi - entry) / risk, (lo - entry) / risk
    return (bars.closes[last] - entry) / risk, "cap", last - i, (hi - entry) / risk, (lo - entry) / risk


def features(bars, i, entry, stop, target, symbol, sector, daily, bench, medians):
    ctx = context_of(bars.upto(i))
    when = bars.times[i]
    out = {"stop %": (entry - stop) / entry * 100,
           "reward:risk": (target - entry) / (entry - stop),
           "rsi14": ctx.rsi14, "volume ratio": ctx.volume_ratio, "atr %": ctx.atr_pct,
           "vs ema200 30m %": (ctx.price / ctx.ema200 - 1) * 100 if ctx.ema200 else None,
           "hour ET": when.hour + when.minute / 60, "slot": slot_of(when),
           "weekday": when.strftime("%a")}
    series = daily.get(symbol)
    stock = daily_before(series, when.date()) if series is not None else None
    base = daily_before(bench, when.date())
    out["stock RS pp"] = out["vs 200-day %"] = out["S&P 1-mo %"] = None
    out["market"] = "?"
    if base is not None:
        b = technicals.compute(base)
        closes = base.closes
        out["S&P 1-mo %"] = (closes[-1] / closes[-22] - 1) * 100 if len(closes) > 22 else None
        sma50 = statistics.fmean(closes[-50:]) if len(closes) >= 50 else None
        sma200 = b.sma200
        if sma50 and sma200:
            up = (closes[-1] > sma50) + (closes[-1] > sma200)
            out["market"] = {2: "above both", 1: "mixed", 0: "below both"}[up]
        if stock is not None:
            f = technicals.compute(stock)
            out["stock RS pp"] = technicals.relative_strength(f, b)
            out["vs 200-day %"] = (stock.closes[-1] / f.sma200 - 1) * 100 if f.sma200 else None
    rs = medians.at(sector, when.date()) if sector else None
    out["sector RS pp"] = rs
    out["sector band"] = ("?" if rs is None else "leading" if rs >= 5
                          else "lagging" if rs <= -5 else "neutral")
    return out


def fmt(v) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}" if isinstance(v, float) else str(v)


async def load(symbols):
    async with httpx.AsyncClient(timeout=60) as client:
        bench = await fetch_bars("^GSPC", client, range_="5y")
        daily, _ = await fetch_many(tuple(symbols), client, range_="5y")
    return bench, daily


def live_part(daily, bench, medians, sector_of) -> None:
    rows = [json.loads(t) for t in open(settings.audit_log_path, encoding="utf-8") if t.strip()]
    sent = {}
    for r in rows:
        intent = r.get("intent") or {}
        if r.get("event") == "order_submitted" and intent.get("client_order_id"):
            sent.setdefault(intent["client_order_id"], intent)
    outcomes = [r["outcome"] for r in rows if r.get("event") == "trade_outcome"]
    history.STORE_DIR = Path("data/intraday")
    table = []
    for o in outcomes:
        intent = sent.get(o.get("client_order_id"), {})
        symbol = o["symbol"]
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        trig = intent.get("trigger_at")
        if bars is None or not trig:
            table.append((o, intent, None, None))
            continue
        t0 = datetime.fromisoformat(trig)
        idx = next((k for k, t in enumerate(bars.times) if t >= t0), None)
        if idx is None:
            table.append((o, intent, None, None))
            continue
        entry = float(o.get("entry_fill") or intent["entry_price"])
        stop, target = float(intent["stop_price"]), float(intent["target_price"])
        feats = features(bars, idx, entry, stop, target, symbol, sector_of.get(symbol),
                         daily, bench, medians)
        table.append((o, intent, feats, travel(bars, idx, entry, stop, target)))
    table.sort(key=lambda x: -float(x[0].get("realized_r") or 0))
    print(f"\n{BOLD}PART 1 -- EVERY CLOSED LIVE TRADE{RESET}  ({len(table)}; levels as sent, "
          f"Yahoo 30m bars, point-in-time context)")
    print(f"  {'sym':<6}{'R':>6} {'exit':<9}{'entry time':<17}{'slot':<6}{'stop%':>6}{'R:R':>5}"
          f"{'RSI':>5}{'vol':>5}{'stkRS':>7}{'secRS':>7} {'sector':<12}{'market':<11}"
          f"{'held':>5}{'MFE':>6}{'MAE':>6}  gemma")
    for o, intent, f, tr in table:
        r = float(o.get("realized_r") or 0)
        tint = GREEN if r > 0 else RED
        if f is None:
            print(f"  {o['symbol']:<6}{tint}{r:>+6.2f}{RESET} {o.get('exit_reason', ''):<9}"
                  f"{DIM}(no bars or order record){RESET}")
            continue
        when = datetime.fromisoformat(intent["trigger_at"])
        print(f"  {o['symbol']:<6}{tint}{r:>+6.2f}{RESET} {o.get('exit_reason', ''):<9}"
              f"{when:%m-%d %a %H:%M} {f['slot']:<6}{f['stop %']:>6.2f}{f['reward:risk']:>5.1f}"
              f"{fmt(f['rsi14']):>5}{fmt(f['volume ratio']):>5}{fmt(f['stock RS pp']):>7}"
              f"{fmt(f['sector RS pp']):>7} {(sector_of.get(o['symbol']) or '')[:11]:<12}"
              f"{f['market']:<11}{tr[2]:>5}{tr[3]:>+6.1f}{tr[4]:>+6.1f}  "
              f"{intent.get('llm_conviction') or '-'}")
    wins = [x for x in table if x[2] and float(x[0].get("realized_r") or 0) > 0]
    losses = [x for x in table if x[2] and float(x[0].get("realized_r") or 0) <= 0]
    print(f"\n  winners {len(wins)}, losers {len(losses)} -- medians:")
    for key in NUMERIC:
        w = [x[2][key] for x in wins if x[2][key] is not None]
        lo = [x[2][key] for x in losses if x[2][key] is not None]
        if w and lo:
            print(f"    {key:<18} winners {statistics.median(w):>+7.2f}   losers "
                  f"{statistics.median(lo):>+7.2f}")
    for name, group in (("winners", wins), ("losers", losses)):
        if group:
            print(f"    {name}: MFE median {statistics.median(x[3][3] for x in group):+.2f}R, "
                  f"MAE median {statistics.median(x[3][4] for x in group):+.2f}R, "
                  f"held median {statistics.median(x[3][2] for x in group):.0f} bars")


def backtest_part(daily, bench, medians, sector_of) -> None:
    history.STORE_DIR = Path("data/intraday_alpaca")
    rows = []
    for symbol in sorted({f.name.split("__")[0] for f in Path("data/intraday_alpaca").glob("*.json")}):
        bars = history.accumulated(symbol, V.interval_minutes, SESSION_ALIGNED)
        if bars is None or len(bars) < 210:
            continue
        for s in scan(bars, k=V.swing_k, require_value_sweep=V.require_value_sweep,
                      min_risk_pct=V.min_risk_pct, min_reward_risk=V.min_reward_risk):
            i = s.trigger_index
            if crosses_session(bars, i) or s.entry_price - s.stop_price <= 0:
                continue
            if bars.times[i].weekday() >= V.wind_down_from_weekday or i + V.max_hold_bars > len(bars) - 1:
                continue
            f = features(bars, i, s.entry_price, s.stop_price, s.target_price, symbol,
                         sector_of.get(symbol), daily, bench, medians)
            tr = travel(bars, i, s.entry_price, s.stop_price, s.target_price)
            rows.append((bars.times[i], f, tr))
    rows.sort(key=lambda r: r[0])
    cut = len(rows) * 2 // 3
    halves = {"train": rows[:cut], "holdout": rows[cut:]}
    print(f"\n{BOLD}PART 2 -- BACKTEST WINNERS VS LOSERS{RESET}  ({len(rows)} setups, Friday "
          f"flatten; train to {rows[cut - 1][0]:%Y-%m-%d}, holdout after)")
    for part, rs in halves.items():
        w = [r for r in rs if r[2][0] > 0]
        print(f"  {part}: {len(w)}/{len(rs)} winners ({len(w) / len(rs) * 100:.0f}%), mean "
              f"{statistics.fmean(r[2][0] for r in rs):+.3f}R; winners' MFE median "
              f"{statistics.median(r[2][3] for r in w):+.2f}R, held median "
              f"{statistics.median(r[2][2] for r in w):.0f} bars")
    print(f"\n  {'feature (median)':<20}{'train W':>9}{'train L':>9}{'hold W':>9}{'hold L':>9}  verdict")
    for key in NUMERIC:
        cells, signs = [], []
        for part in ("train", "holdout"):
            w = [r[1][key] for r in halves[part] if r[2][0] > 0 and r[1][key] is not None]
            lo = [r[1][key] for r in halves[part] if r[2][0] <= 0 and r[1][key] is not None]
            mw, ml = statistics.median(w), statistics.median(lo)
            cells += [mw, ml]
            signs.append(mw > ml)
        verdict = f"{GREEN}STABLE: winners {'higher' if signs[0] else 'lower'}{RESET}" \
            if signs[0] == signs[1] else f"{DIM}flips{RESET}"
        print(f"  {key:<20}" + "".join(f"{c:>+9.2f}" for c in cells) + f"  {verdict}")
    print(f"\n  {'category':<24}{'train mean R (n)':>20}{'holdout mean R (n)':>22}")
    for key in CATEGORIC:
        groups = defaultdict(lambda: {"train": [], "holdout": []})
        for part, rs in halves.items():
            for r in rs:
                groups[r[1][key]][part].append(r[2][0])
        for value in sorted(groups, key=str):
            g = groups[value]
            if len(g["train"]) < 20 or len(g["holdout"]) < 10:
                continue
            t, h = statistics.fmean(g["train"]), statistics.fmean(g["holdout"])
            print(f"  {key + ' = ' + str(value):<24}{t:>+12.3f} ({len(g['train']):>4})"
                  f"{h:>+14.3f} ({len(g['holdout']):>4})")


async def main() -> int:
    sector_of = sectors()
    symbols = sorted({f.name.split("__")[0] for f in Path("data/intraday_alpaca").glob("*.json")})
    bench, daily = await load(symbols)
    medians = SectorMedians(daily, sector_of, list(bench.dates), list(bench.closes))
    live_part(daily, bench, medians, sector_of)
    backtest_part(daily, bench, medians, sector_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
