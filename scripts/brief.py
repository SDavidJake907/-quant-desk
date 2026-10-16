"""Everything the system knows about one symbol, on one page.

    python scripts/brief.py AMD
    python scripts/brief.py AMD NVDA MU

What matters for a manual pick or a momentum holding: returns, 12-1
momentum against the S&P, volatility for placing a stop, liquidity, the
calendar risks, and structural news.

ADVISORY, NOT PRIORITY (WORKLIST §38)
RSI and the 50/200-day averages were measured here with no edge -- classic TA
|t| < 1.7 inside momentum (§31), RSI/MACD/ADX entries significantly WORSE than
random (§35) -- so they sit in a fenced SECOND OPINIONS block at the bottom,
labelled, beside TradingAgents' latest (unproven, graded) call. No verdict
words like "overbought": that reading argues against the one rule that held
up. The sweep-setup block is gone; the sweep is paused (-0.041R unseen, §30).
Full page: scripts/second_opinion.py.

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

import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution.news_gate import check as check_news
from scanner.features import technicals
from scanner.features.bars import fetch_bars
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import atr_pct, rsi
from scanner.advisor import opinions as op
from scanner.execution.scorecard import read_rows
from scanner.execution.policy import TradingPolicy
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

    # ---- returns and momentum -------------------------------------------
    print(f"\n  {BOLD}RETURNS AND MOMENTUM{RESET} {DIM}(daily){RESET}")
    _row("last close", f"{price:,.2f}")
    for label, back in (("1 month", 21), ("3 month", 63), ("12 month", 252)):
        if len(closes) > back:
            change = (price / closes[-back - 1] - 1) * 100
            _row(f"{label} return", f"{change:+.1f}%")
    if feats.momentum_12_1 is not None:
        _row("12-1 momentum", f"{feats.momentum_12_1:+.1f}%",
             "what the momentum engine ranks on")
    rs = technicals.relative_strength(feats, bench) if bench else None
    if rs is not None:
        _row("vs S&P 500 (12-1)", f"{rs:+.1f}pp",
             "leading" if rs > 0 else "lagging")
    if member:
        _row("sector", member.sector)

    # ---- volatility, for placing a stop ----------------------------------
    print(f"\n  {BOLD}VOLATILITY{RESET} {DIM}(for placing a stop, not a signal){RESET}")
    atr = atr_pct(daily)
    _row("ATR% daily", f"{atr:.2f}%" if atr else "unknown",
         f"a stop inside ~1 ATR ({price * atr / 100:,.2f}) is noise" if atr else "")
    intra = atr_pct(bars)
    _row(f"ATR% {policy.interval_minutes}m", f"{intra:.2f}%" if intra else "unknown",
         "typical bar range")

    # ---- liquidity -------------------------------------------------------
    window = bars.closes[-20:], bars.volumes[-20:]
    dv = sum(v * c for c, v in zip(*window)) / max(1, len(window[0]))
    print(f"\n  {BOLD}LIQUIDITY{RESET}")
    _row("20-bar $ volume", f"{dv:,.0f}",
         f"{'passes' if dv >= policy.min_dollar_volume else 'FAILS'} the "
         f"{policy.min_dollar_volume:,.0f} floor")
    _row("price floor", f"{policy.min_price:,.2f}",
         "passes" if price >= policy.min_price else "FAILS")

    # ---- calendar risk ---------------------------------------------------
    print(f"\n  {BOLD}CALENDAR{RESET}")
    try:
        found, _failed = await event_calendar.upcoming_earnings(frozenset({symbol}), client)
        report = found.get(symbol)
        if report is None:
            _row("earnings", "none in window")
        else:
            days = (report.on - dt.date.today()).days
            _row("earnings", f"{report.on:%Y-%m-%d} {report.when}",
                 f"{days}d away" + (" -- a hold now carries the report gap"
                                    if 0 <= days <= 7 else ""))
    except Exception:
        _row("earnings", "unknown", "calendar unavailable — treat as unknown")
    event = ex_div.get(symbol)
    if event:
        _row("ex-dividend", str(event.ex_date),
             f"{event.cash:.3f} per share, price drops ~{event.drop_pct(price):.2f}%")
    else:
        _row("ex-dividend", "none declared")
    history = await fetch_dividends(symbol, client)
    if history and history.pays_dividends:
        _row("dividend yield", f"{history.yield_pct():.2f}%",
             f"{history.growth_streak()}y raise streak, "
             f"{'CUT in window' if history.was_cut() else 'no cuts'}")

    # ---- news, read by Gemma ---------------------------------------------
    print(f"\n  {BOLD}NEWS{RESET} {DIM}(only a merger or other structural event is "
          f"flagged: news labels did not predict returns here, news_event_test){RESET}")
    try:
        flag = await check_news(symbol, client)
        verdict = flag.verdict()
        if not flag.headlines:
            print(f"    {DIM}no recent headlines{RESET}")
        for headline in flag.headlines[:5]:
            print(f"    {DIM}- {str(headline)[:88]}{RESET}")
        cats = ", ".join(sorted(set(flag.categories))) or "nothing material"
        _row("categories", cats)
        _row("structural", "none" if verdict else "FLAGGED",
             verdict.reason if not verdict else "")
    except Exception as exc:
        _row("news", "unavailable", f"{type(exc).__name__} — treated as unknown")

    # ---- second opinions: advisory, not priority --------------------------
    print(f"\n  {BOLD}SECOND OPINIONS{RESET} {DIM}(advisory, not priority -- never "
          f"block, size or close; full page: scripts/second_opinion.py {symbol}){RESET}")
    calls = op.latest_calls(read_rows(settings.audit_log_path), symbol)
    ta = calls.get("tradingagents")
    _row("TradingAgents", f"{ta['call']} {ta['at']}" if ta else "no call",
         f"[{op.UNPROVEN}] graded, not yet proven")
    value = rsi(closes)
    _row("RSI(14) daily", f"{value:.1f}" if value else "unknown",
         f"[{op.NOISE}] §35, §31")
    for n in (50, 200):
        if len(closes) >= n:
            gap = (price / (sum(closes[-n:]) / n) - 1) * 100
            _row(f"vs {n}-day average", f"{gap:+.1f}%", f"[{op.NOISE}] §31, §12")

    # ---- what the evidence says ------------------------------------------
    print(f"\n  {BOLD}WHAT THE EVIDENCE SUPPORTS{RESET}")
    print(f"    {DIM}One rule held up out of sample: buying the top 10 by 12-1 momentum,")
    print(f"    in cash when the S&P is under its 210-day average -- +2.38pp a")
    print(f"    3-month hold over random picks 2016-26, t 1.49, worst hold -36.9%.")
    print(f"    Nothing on this page predicts a single stock's next move.{RESET}")


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
    print(f"\n{YELLOW}This is a briefing, not a recommendation. Size a pick with")
    print(f"scripts/manual_trade.py: it comes from the stop distance, never conviction.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="+")
    p.add_argument("--equity", type=float, default=99_000.0)
    a = p.parse_args()
    asyncio.run(main(tuple(a.symbols), a.equity))
