"""The sweep scanner as a ghost book: its own rules replayed on paper.

RULES, SET 2026-10-19 BEFORE ANY GHOST TRADE WAS SCORED
- Universe: the S&P 500, where its 16 live trades came from (paused 10-06).
- Setups: screens.smc.scan on 30-minute bars under the window's TradingPolicy
  (k, stop >= min_risk_pct, reward:risk >= min_reward_risk, value-sweep flag),
  liquid names only -- the same call the live scan made.
- Entry: the close of the trigger bar, the price the holdout measured. Refused
  when that bar ends a session (block_overnight_entry).
- Slots: at most policy.max_positions open, policy.max_per_sector per sector,
  one per symbol; setups on the same bar ranked by smc.priority.
- Exit: stop, target, or max_hold_bars, and flat on the last bar before a
  weekend (flat_before_weekend); a bar that opens through a level fills at
  the open (backtest.setups.resolve).
- NOT replayed: the LLM news veto and the earnings block (they need a model
  or a calendar as it stood at that moment). Its own slots only: names the
  real books hold do not block it.
- Starts 2026-10-07 with the competition's other paper books. Scored in R and
  as the equal-weight % per trade, shown beside the standings, not in the
  head to head.

PURE. scripts/ghost_scanner.py fetches bars and runs the scan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from scanner.backtest.setups import resolve
from scanner.execution.policy import TradingPolicy
from scanner.execution.session import bars_until_weekend, crosses_session
from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.models import Frozen
from scanner.screens.smc import Setup, priority

START = datetime(2026, 10, 7, tzinfo=EXCHANGE_TZ)


class GhostTrade(Frozen):
    symbol: str
    sector: str
    entry_at: datetime
    entry: float
    stop: float
    target: float
    exit_at: datetime | None = None
    exit_price: float | None = None
    reason: str | None = None

    @property
    def r(self) -> float:
        """Realised R; 0 while open."""
        return marked_r(self, self.exit_price) if self.exit_price is not None else 0.0


class Summary(Frozen):
    closed: int
    won: int
    open: int
    total_r: float
    open_r: float
    mean_pct: float | None


def marked_r(trade: GhostTrade, price: float) -> float:
    risk = trade.entry - trade.stop
    return (price - trade.entry) / risk if risk > 0 else 0.0


def _settle(bars: IntradayBars, trade: GhostTrade, index: int, policy: TradingPolicy) -> GhostTrade:
    cap = policy.max_hold_bars
    if policy.flat_before_weekend:
        to_weekend = bars_until_weekend(bars, index)
        if to_weekend is not None:
            cap = min(cap, to_weekend)
    out = resolve(bars, symbol=trade.symbol, entry_index=index, entry_price=trade.entry,
                  stop_price=trade.stop, target_price=trade.target, max_bars=cap)
    if out is None:
        return trade
    return trade.model_copy(update={"exit_at": bars.times[out.exit_index],
                                    "exit_price": out.exit_price, "reason": out.reason})


def _update(trade: GhostTrade, bars: IntradayBars | None, policy: TradingPolicy) -> GhostTrade:
    if trade.exit_at is not None or bars is None or trade.entry_at not in bars.times:
        return trade
    return _settle(bars, trade, bars.times.index(trade.entry_at), policy)


def _candidates(bars: Mapping[str, IntradayBars], setups: Mapping[str, tuple[Setup, ...]],
                policy: TradingPolicy, since: datetime) -> list[tuple[datetime, float, str, Setup]]:
    out = []
    for symbol, found in setups.items():
        b = bars.get(symbol)
        if b is None:
            continue
        for s in found:
            at = b.times[s.trigger_index]
            if at <= since or at.hour >= 16:
                continue
            if policy.block_overnight_entry and crosses_session(b, s.trigger_index):
                continue
            out.append((at, priority(s), symbol, s))
    return sorted(out, key=lambda row: (row[0], -row[1], row[2]))


def replay(bars: Mapping[str, IntradayBars], setups: Mapping[str, tuple[Setup, ...]],
           sectors: Mapping[str, str], policy: TradingPolicy, *, since: datetime,
           existing: tuple[GhostTrade, ...]) -> tuple[GhostTrade, ...]:
    """Settle the open ghost trades, then take the setups after `since` in time order."""
    trades = [_update(t, bars.get(t.symbol), policy) for t in existing]
    for at, _, symbol, s in _candidates(bars, setups, policy, since):
        live = [t for t in trades if t.entry_at <= at and (t.exit_at is None or t.exit_at > at)]
        sector = sectors.get(symbol, "")
        if (any(t.symbol == symbol for t in live) or len(live) >= policy.max_positions
                or (policy.max_per_sector and sector
                    and sum(t.sector == sector for t in live) >= policy.max_per_sector)):
            continue
        new = GhostTrade(symbol=symbol, sector=sector, entry_at=at, entry=s.entry_price,
                         stop=s.stop_price, target=s.target_price)
        trades.append(_settle(bars[symbol], new, s.trigger_index, policy))
    return tuple(trades)


def summary(trades: tuple[GhostTrade, ...], month: str,
            last_close: Mapping[str, float]) -> Summary:
    """Trades entered in `month` (YYYY-MM, exchange time)."""
    mine = [t for t in trades if t.entry_at.astimezone(EXCHANGE_TZ).strftime("%Y-%m") == month]
    closed = [t for t in mine if t.exit_price is not None]
    still = [t for t in mine if t.exit_price is None]
    pcts = [(t.exit_price / t.entry - 1) * 100 for t in closed]
    pcts += [(last_close[t.symbol] / t.entry - 1) * 100 for t in still if t.symbol in last_close]
    return Summary(closed=len(closed), won=sum(t.r > 0 for t in closed), open=len(still),
                   total_r=sum(t.r for t in closed),
                   open_r=sum(marked_r(t, last_close[t.symbol]) for t in still
                              if t.symbol in last_close),
                   mean_pct=sum(pcts) / len(pcts) if pcts else None)


def dump(trades: tuple[GhostTrade, ...], *, through: datetime) -> str:
    return json.dumps({"through": through.isoformat(),
                       "trades": [t.model_dump(mode="json") for t in trades]}, indent=1)


def load(text: str) -> tuple[tuple[GhostTrade, ...], datetime | None]:
    data = json.loads(text)
    through = datetime.fromisoformat(data["through"]) if data.get("through") else None
    return tuple(GhostTrade.model_validate(t) for t in data.get("trades", [])), through
