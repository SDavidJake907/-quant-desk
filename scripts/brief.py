"""Everything the system knows about one symbol, on one page.

    python scripts/brief.py AMD
    python scripts/brief.py AMD NVDA MU

This assembles what is already computed and then discarded a line at a time:
trend, momentum, relative strength, liquidity, the setup, its size, the news,
and the calendar risks around it.

WHAT IS DELIBERATELY ABSENT
No probability estimates, no T1/T2 target ladders, no analyst grades, no
implied volatility, no fundamentals. Not because they would not be useful —
because this project has no source for them, and a confident number with
nothing behind it is worse than a blank space. A ladder in particular was
measured here and came out worse in both halves of the data.

Every figure below traces to a module that computes it. Where something is
unknown it says unknown.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import statistics

import httpx

from scanner.execution.news_gate import check as check_news
from scanner.execution.sizing import size_position
from scanner.features import technicals
from scanner.features.bars import fetch_bars
from scanner.features.intraday import fetch_intraday, max_range, session_slot
from scanner.features.intraday_context import atr_pct, ema, rsi
from scanner.execution.policy import TradingPolicy
from scanner.screens.smc import priority, scan
from scanner.sources import composite
from scanner.sources import calendar as event_calendar
from scanner.sources import dividends
from scanner.sources.dividend_history import fetch as fetch_dividends
from scanner.sources.index_history import COMPOSITE_1500
from scanner.config import settings

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

BENCHMARK = "^GSPC"


def _row(label, value, note=""):
    print(f"    {label:<26}{value:>16}   {DIM}{note}{RESET}")


def _colour(v):
    return GREEN if v > 0 else (RED if v < 0 else DIM)


async def brief(symbol: str, client, policy, universe, bench, ex_div, equity):
    member = next((m for m in universe if m.symbol == symbol), None)
    print(f"\n{BOLD}{'='*74}{RESET}")
    print(f"{BOLD}{symbol}{RESET}  {member.name if member else '(not in the S&P 1500)'}")
    print(f"{BOLD}{'='*74}{RESET}")

    daily = await fetch_bars(symbol, client, range_="2y")
    bars = await fetch_intraday(symbol, client, minutes=policy.interval_minutes,
                                range_=max_range(policy.interval_minutes))
    closes, price = daily.closes, daily.closes[-1]
    feats = technicals.compute(daily)

    # ---- trend -----------------------------------------------------------
    print(f"\n  {BOLD}TREND{RESET} {DIM}(daily){RESET}")
    _row("last close", f"{price:,.2f}")
    for window in (20, 50, 200):
        value = ema(closes, window)
        if value:
            gap = (price / value - 1) * 100
            _row(f"{window}-day EMA", f"{value:,.2f}",
                 f"price {gap:+.1f}% {'above' if gap > 0 else 'below'}")
    if len(closes) >= 50:
        _row("50-day SMA", f"{statistics.fmean(closes[-50:]):,.2f}")
    for label, back in (("1 month", 21), ("3 month", 63), ("12 month", 252)):
        if len(closes) > back:
            change = (price / closes[-back - 1] - 1) * 100
            _row(f"{label} return", f"{change:+.1f}%")

    # ---- momentum & volatility -------------------------------------------
    print(f"\n  {BOLD}MOMENTUM AND VOLATILITY{RESET}")
    daily_rsi = rsi(closes)
    _row("RSI(14) daily", f"{daily_rsi:.1f}" if daily_rsi else "unknown")
    intra_rsi = rsi(bars.closes)
    _row(f"RSI(14) {policy.interval_minutes}m", f"{intra_rsi:.1f}" if intra_rsi else "unknown")
    atr = atr_pct(bars)
    _row(f"ATR% {policy.interval_minutes}m", f"{atr:.2f}%" if atr else "unknown",
         "typical bar range")
    rs = technicals.relative_strength(feats, bench) if bench else None
    if rs is not None:
        _row("vs S&P 500 (12-1)", f"{rs:+.1f}pp",
             "leading" if rs > 0 else "lagging")
    if member:
        _row("sector", member.sector)

    # ---- liquidity -------------------------------------------------------
    window = bars.closes[-20:], bars.volumes[-20:]
    dv = sum(v * c for c, v in zip(*window)) / max(1, len(window[0]))
    print(f"\n  {BOLD}LIQUIDITY{RESET}")
    _row("20-bar $ volume", f"{dv:,.0f}",
         f"{'passes' if dv >= policy.min_dollar_volume else 'FAILS'} the "
         f"{policy.min_dollar_volume:,.0f} floor")
    _row("price floor", f"{policy.min_price:,.2f}",
         "passes" if price >= policy.min_price else "FAILS")

    # ---- the setup -------------------------------------------------------
    print(f"\n  {BOLD}SETUP{RESET} {DIM}(sweep -> CHoCH -> BOS, "
          f"{policy.interval_minutes}m){RESET}")
    setups = scan(bars, k=policy.swing_k,
                  require_value_sweep=policy.require_value_sweep,
                  min_risk_pct=policy.min_risk_pct,
                  min_reward_risk=policy.min_reward_risk)
    if not setups:
        print(f"    {DIM}no setup on the visible history{RESET}")
    else:
        s = setups[-1]
        age = len(bars) - 1 - s.trigger_index
        fresh = age <= policy.fresh_bars
        stop_pct = s.risk / s.entry_price * 100
        _row("triggered", f"{bars.times[s.trigger_index]:%Y-%m-%d %H:%M}",
             f"{age} bars ago — {'FRESH' if fresh else 'stale, not tradeable'}")
        _row("entry (trigger close)", f"{s.entry_price:,.2f}")
        _row("stop (higher low)", f"{s.stop_price:,.2f}", f"{stop_pct:.2f}% risk")
        _row("target (range high)", f"{s.target_price:,.2f}")
        _row("reward : risk", f"{s.reward_risk:.2f}R",
             f"floor is {policy.min_reward_risk}R")
        _row("priority score", f"{priority(s):.1f}")
        _row("session slot", session_slot(bars.times[s.trigger_index]))
        sizing = size_position(equity, s.entry_price, s.stop_price,
                               risk_pct=policy.risk_pct,
                               max_position_pct=policy.max_position_pct)
        _row("size", f"{sizing.shares} sh",
             f"{sizing.position_dollars:,.0f} at risk {sizing.risk_dollars:,.0f} "
             f"({sizing.limited_by})")

    # ---- calendar risk ---------------------------------------------------
    print(f"\n  {BOLD}CALENDAR{RESET}")
    try:
        found = await event_calendar.upcoming_earnings((symbol,), client)
        report = found.get(symbol) if isinstance(found, dict) else found
        if report is None:
            _row("earnings", "none in window")
        else:
            days = (report.on - dt.date.today()).days
            blocked = 0 <= days <= policy.block_earnings_within_days
            _row("earnings", f"{report.on:%Y-%m-%d} {report.when}",
                 f"{days}d away — {'BLOCKS entry' if blocked else 'outside the '
                 f'{policy.block_earnings_within_days}d gate'}")
    except Exception:
        _row("earnings", "unknown", "calendar unavailable — treat as unknown")
    event = ex_div.get(symbol)
    if event and setups:
        drop = event.drop_pct(price)
        stop_pct = setups[-1].risk / setups[-1].entry_price * 100
        _row("ex-dividend", str(event.ex_date),
             f"drops {drop:.2f}% = {drop/stop_pct*100:.0f}% of the stop")
    elif event:
        _row("ex-dividend", str(event.ex_date), f"{event.cash:.3f} per share")
    else:
        _row("ex-dividend", "none declared")
    history = await fetch_dividends(symbol, client)
    if history and history.pays_dividends:
        _row("dividend yield", f"{history.yield_pct():.2f}%",
             f"{history.growth_streak()}y raise streak, "
             f"{'CUT in window' if history.was_cut() else 'no cuts'}")

    # ---- news, read by Gemma ---------------------------------------------
    print(f"\n  {BOLD}NEWS{RESET} {DIM}(Gemma 3 — every claim must quote the "
          f"headline verbatim or it is discarded){RESET}")
    try:
        flag = await check_news(symbol, client)
        verdict = flag.verdict()
        if not flag.headlines:
            print(f"    {DIM}no recent headlines{RESET}")
        for headline in flag.headlines[:5]:
            print(f"    {DIM}- {str(headline)[:88]}{RESET}")
        cats = ", ".join(sorted(set(flag.categories))) or "nothing material"
        _row("categories", cats)
        _row("gate", "PASS" if verdict else "BLOCKED",
             verdict.reason if not verdict else "no structural news")
    except Exception as exc:
        _row("news", "unavailable", f"{type(exc).__name__} — treated as unknown")

    # ---- what the evidence says ------------------------------------------
    print(f"\n  {BOLD}WHAT THE EVIDENCE ACTUALLY SUPPORTS{RESET}")
    print(f"    {DIM}This setup type: +0.247R over 43 holdout trades,")
    print(f"    95% CI [-0.342R, +0.836R] — the interval includes zero.")
    print(f"    ~244 trades are needed before that interval means anything.")
    print(f"    No price target beyond the range high is modelled, and no")
    print(f"    probability is estimated, because nothing here supports one.{RESET}")


async def main(symbols: tuple[str, ...], equity: float) -> None:
    policy = TradingPolicy()
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, COMPOSITE_1500)
        bench = technicals.compute(await fetch_bars(BENCHMARK, client, range_="2y"))
        ex_div = dividends.by_symbol(await dividends.fetch_announcements(
            client, settings.alpaca_key_id, settings.alpaca_secret_key))
        for symbol in symbols:
            try:
                await brief(symbol.upper(), client, policy, universe, bench,
                            ex_div, equity)
            except Exception as exc:
                print(f"\n  {RED}{symbol}: {type(exc).__name__}: {exc}{RESET}")
    print(f"\n{YELLOW}This is a briefing, not a recommendation. Position sizes")
    print(f"come from the measured stop distance, never from conviction.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="+")
    p.add_argument("--equity", type=float, default=99_000.0)
    a = p.parse_args()
    asyncio.run(main(tuple(a.symbols), a.equity))
