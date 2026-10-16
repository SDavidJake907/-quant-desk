"""Portfolio review for Gemma — read-only, advisory, never enforced.

THE IDEA
Every morning Gemma reviews the whole account before she decides what to buy.
She sees: what's open, what's closed, what the news gate refused, what
TradingAgents said about her holdings, and how each book is doing. Nothing in
this report can block, size, or close a trade — it's context for her decision,
same as the scoreboard and the daily brief.

PURE BY DEFAULT
The core functions read parsed rows and broker records; they place no orders,
cancel nothing, and touch no policy. The only side effect is writing the
review file, which Gemma reads at 05:45 AK.

WHAT SHE SEES
  - Account: equity, buying power, status
  - Open positions: symbol, qty, market value, unrealized P&L, owner book,
    entry price, stop, target
  - Working orders: symbol, side, type, limit/stop prices, owner book
  - By book: trades, wins, total R, mean R, win rate, open positions
  - Recent activity: entries, exits, refusals (last 3 sessions)
  - Sector exposure: positions and market value per sector
  - News gate refusals: what was blocked and why
  - TradingAgents ratings: what she said about held names
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scanner.config import settings
from scanner.execution.ownership import GEMMA, MOMENTUM, PICKS, SMC, strategy_of
from scanner.models import Frozen

#: How many sessions back the review looks for recent activity.
LOOKBACK_SESSIONS = 3


class Position(Frozen):
    """One open position as Gemma sees it."""

    symbol: str
    qty: int
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    owner: str
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None


class WorkingOrder(Frozen):
    """One working order as Gemma sees it."""

    symbol: str
    side: str
    type: str
    limit_price: float | None = None
    stop_price: float | None = None
    owner: str = ""


class BookStats(Frozen):
    """Closed-trade stats for one book."""

    strategy: str
    trades: int
    wins: int
    total_r: float
    mean_r: float
    win_rate: float
    open_positions: int


class SectorExposure(Frozen):
    """What one sector holds across the account."""

    sector: str
    positions: int
    market_value: float
    pct_of_account: float


class Refusal(Frozen):
    """One news-gate or model refusal."""

    symbol: str
    reason: str
    day: str


class TAviso(Frozen):
    """One TradingAgents rating on a held name."""

    symbol: str
    rating: str
    trader: str
    day: str


class PortfolioReview(Frozen):
    """The complete read-only review Gemma reads before deciding."""

    date: str
    account_equity: float
    account_buying_power: float
    account_status: str
    positions: tuple[Position, ...] = ()
    working_orders: tuple[WorkingOrder, ...] = ()
    by_book: tuple[BookStats, ...] = ()
    recent_entries: tuple[str, ...] = ()
    recent_exits: tuple[str, ...] = ()
    recent_refusals: tuple[Refusal, ...] = ()
    sector_exposure: tuple[SectorExposure, ...] = ()
    news_gate_refusals: tuple[Refusal, ...] = ()
    tradingagents_ratings: tuple[TAviso, ...] = ()

    def summary_text(self) -> str:
        """English summary for Gemma's facts block. Advisory only."""
        lines: list[str] = []
        lines.append(f"Portfolio as of {self.date}:")
        lines.append(
            f"  Account equity {self.account_equity:,.2f}, "
            f"buying power {self.account_buying_power:,.2f}, "
            f"status {self.account_status}."
        )
        if self.positions:
            lines.append(f"  Open positions ({len(self.positions)}):")
            for p in self.positions:
                lines.append(
                    f"    {p.symbol:<6} {p.qty:>5} sh  "
                    f"{p.market_value:>10,.2f}  "
                    f"{p.unrealized_pl:+.2f} ({p.unrealized_plpc:+.1f}%)  "
                    f"owner={p.owner}"
                )
        else:
            lines.append("  Open positions: none.")
        if self.by_book:
            lines.append("  By book (closed trades):")
            for b in self.by_book:
                lines.append(
                    f"    {b.strategy:<8} n={b.trades:3d}  "
                    f"wins={b.wins}  total_R={b.total_r:+.2f}  "
                    f"mean_R={b.mean_r:+.3f}  win_rate={b.win_rate:.0%}  "
                    f"open={b.open_positions}"
                )
        if self.recent_refusals:
            lines.append("  Recent refusals (news gate / model / sector cap):")
            for r in self.recent_refusals:
                lines.append(f"    {r.day} {r.symbol:<6} {r.reason}")
        if self.news_gate_refusals:
            lines.append("  News gate refusals:")
            for r in self.news_gate_refusals:
                lines.append(f"    {r.day} {r.symbol:<6} {r.reason}")
        if self.tradingagents_ratings:
            lines.append("  TradingAgents on holdings:")
            for t in self.tradingagents_ratings:
                lines.append(f"    {t.day} {t.symbol:<6} {t.rating} "
                             f"(trader={t.trader})")
        if self.sector_exposure:
            lines.append("  Sector exposure:")
            for s in self.sector_exposure:
                lines.append(
                    f"    {s.sector:<20} {s.positions:2d} pos  "
                    f"{s.market_value:>10,.2f}  {s.pct_of_account:.1f}% of acct"
                )
        lines.append(
            "  Advisory only: nothing above blocks, sizes, or closes a trade."
        )
        return "\n".join(lines)


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return None


def _day(stamp: str | None) -> str:
    if not stamp:
        return ""
    return str(stamp)[:10]


def _is_recent(when: float | None, lookback_sessions: int) -> bool:
    """Whether `when` is within the last N trading sessions."""
    if when is None:
        return False
    cutoff = datetime.now().astimezone() - timedelta(days=lookback_sessions * 3)
    return datetime.fromtimestamp(when, tz=datetime.now().astimezone().tzinfo) >= cutoff


def _owner_of(symbol: str, owner_map: dict[str, str]) -> str:
    return owner_map.get(symbol, "unknown")


def _levels_for_symbol(
    symbol: str, intents: dict[str, dict]
) -> tuple[float | None, float | None]:
    """Entry/stop/target from the most recent intent for `symbol`."""
    best: dict | None = None
    best_when: float | None = None
    for coid, intent in intents.items():
        if str(intent.get("symbol", "")).upper() != symbol.upper():
            continue
        when = _epoch(str(intent.get("trigger_at") or ""))
        if when is None:
            continue
        if best_when is None or when > best_when:
            best = intent
            best_when = when
    if best is None:
        return None, None, None
    return (
        float(best.get("entry_price") or 0) or None,
        float(best.get("stop_price") or 0) or None,
        float(best.get("target_price") or 0) or None,
    )


def build_review(
    rows: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    account: dict[str, Any],
    owner_map: dict[str, str],
    sector_of: dict[str, str],
) -> PortfolioReview:
    """Build a read-only review from audit rows and broker records.

    Places no orders, cancels nothing, touches no policy.
    """
    intents: dict[str, dict] = {}
    for row in rows:
        intent = row.get("intent") or {}
        coid = str(intent.get("client_order_id") or row.get("order_id") or "")
        event = str(row.get("event", ""))
        if coid and event in ("trade_intent", "order_submitted"):
            intents.setdefault(coid, intent)

    # --- positions ---
    pos_list: list[Position] = []
    for p in positions:
        symbol = str(p.get("symbol") or "")
        mv = float(p.get("market_value") or 0.0)
        upl = float(p.get("unrealized_pl") or 0.0)
        uplpc = float(p.get("unrealized_plpc") or 0.0)
        entry, stop, target = _levels_for_symbol(symbol, intents)
        pos_list.append(
            Position(
                symbol=symbol,
                qty=int(float(p.get("qty") or 0)),
                market_value=mv,
                unrealized_pl=upl,
                unrealized_plpc=uplpc,
                owner=_owner_of(symbol, owner_map),
                entry_price=entry,
                stop_price=stop,
                target_price=target,
            )
        )
    pos_list.sort(key=lambda p: -p.market_value)

    # --- working orders ---
    wo_list: list[WorkingOrder] = []
    for o in orders:
        coid = str(o.get("client_order_id") or "")
        wo_list.append(
            WorkingOrder(
                symbol=str(o.get("symbol") or ""),
                side=str(o.get("side") or ""),
                type=str(o.get("type") or ""),
                limit_price=float(o.get("limit_price") or 0) or None,
                stop_price=float(o.get("stop_price") or 0) or None,
                owner=strategy_of(coid) if coid else "unknown",
            )
        )
    wo_list.sort(key=lambda o: o.symbol)

    # --- by book (closed trades) ---
    seen: set[str] = set()
    acc: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0])
    open_by_book: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.get("event") != "trade_outcome":
            continue
        out = row.get("outcome") or {}
        coid = str(out.get("client_order_id") or "")
        if not coid or coid in seen:
            continue
        seen.add(coid)
        r = float(out.get("realized_r") or 0.0)
        name = strategy_of(coid)
        v = acc[name]
        v[0] += 1
        v[1] += 1 if r > 0 else 0
        v[2] += r
        v[3] += float(out.get("realized_dollars") or 0.0)
    for p in pos_list:
        open_by_book[p.owner] += 1
    book_stats: list[BookStats] = []
    for name, (n, wins, total_r, total_d) in sorted(acc.items()):
        book_stats.append(
            BookStats(
                strategy=name,
                trades=n,
                wins=int(wins),
                total_r=total_r,
                mean_r=total_r / n if n else 0.0,
                win_rate=wins / n if n else 0.0,
                open_positions=open_by_book.get(name, 0),
            )
        )

    # --- recent activity ---
    cutoff_s = LOOKBACK_SESSIONS * 3 * 86_400
    now_ts = datetime.now().astimezone().timestamp()
    entries: list[str] = []
    exits: list[str] = []
    refusals: list[Refusal] = []
    ta_ratings: list[TAviso] = []
    news_refusals: list[Refusal] = []
    for row in rows:
        when = _epoch(str(row.get("at", "")))
        if when is None or now_ts - when > cutoff_s:
            continue
        day = _day(str(row.get("at", "")))
        event = str(row.get("event", ""))
        symbol = str(row.get("symbol", ""))
        reason = str(row.get("reason", ""))
        if event == "order_submitted":
            intent = row.get("intent") or {}
            strat = str(intent.get("strategy", "?"))
            entries.append(f"{day} {symbol:<6} {strat}")
        elif event == "trade_outcome":
            out = row.get("outcome") or {}
            exits.append(
                f"{day} {symbol:<6} {str(out.get('exit_reason') or '?'):<10} "
                f"{float(out.get('realized_r') or 0):+.2f}R"
            )
        elif event == "candidate_refused":
            refusals.append(Refusal(symbol=symbol, reason=reason, day=day))
        elif event == "news_gate_degraded":
            refusals.append(
                Refusal(symbol=symbol or "ALL", reason=reason, day=day)
            )
        elif event == "tradingagents_rating":
            parts = reason.split(";", 1)
            rating = parts[0].strip() if parts else ""
            trader = ""
            for part in parts[1:] if len(parts) > 1 else []:
                if part.strip().startswith("trader="):
                    trader = part.strip().split("=", 1)[1]
            ta_ratings.append(
                TAviso(symbol=symbol, rating=rating, trader=trader, day=day)
            )
        elif event == "gemma_refused":
            refusals.append(Refusal(symbol=symbol, reason=reason, day=day))

    # --- sector exposure ---
    equity = float(account.get("equity") or 0.0)
    sector_acc: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for p in pos_list:
        sector = sector_of.get(p.symbol, "Unclassified")
        v = sector_acc[sector]
        v[0] += 1
        v[1] += p.market_value
    sector_list: list[SectorExposure] = []
    for sector, (n, mv) in sorted(sector_acc.items()):
        sector_list.append(
            SectorExposure(
                sector=sector,
                positions=n,
                market_value=mv,
                pct_of_account=(mv / equity * 100 if equity else 0.0),
            )
        )
    sector_list.sort(key=lambda s: -s.market_value)

    return PortfolioReview(
        date=date.today().isoformat(),
        account_equity=float(account.get("equity") or 0.0),
        account_buying_power=float(account.get("buying_power") or 0.0),
        account_status=str(account.get("status") or "UNKNOWN"),
        positions=tuple(pos_list),
        working_orders=tuple(wo_list),
        by_book=tuple(book_stats),
        recent_entries=tuple(entries[-10:]),
        recent_exits=tuple(exits[-10:]),
        recent_refusals=tuple(refusals[-10:]),
        sector_exposure=tuple(sector_list),
        news_gate_refusals=tuple(news_refusals[-10:]),
        tradingagents_ratings=tuple(ta_ratings[-10:]),
    )


def review_text(review: PortfolioReview) -> str:
    """Plain-text review for logging and display."""
    lines: list[str] = [review.summary_text()]
    if review.working_orders:
        lines.append("  Working orders:")
        for o in review.working_orders:
            lines.append(
                f"    {o.symbol:<6} {o.side:<5} {o.type:<8} "
                f"limit={o.limit_price} stop={o.stop_price} owner={o.owner}"
            )
    return "\n".join(lines)
