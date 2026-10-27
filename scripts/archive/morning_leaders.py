"""Morning leaders: the momentum leaders, where the money is moving, two lists of 4.

    python scripts/morning_leaders.py            # write logs/leaders/YYYY-MM-DD.md
    python scripts/morning_leaders.py --no-write # print only, record nothing

Weekdays 05:00 AK (stock-scanner-leaders), before the open and Gemma's 05:45.
REPORT ONLY (operator's choice 2026-10-18): it never edits picks.txt and never
sends an order; the operator decides what goes on the list and commits it.

  1  rank today's S&P 500 + MidCap 400 by 12-1 momentum, closes up to the last
     completed session -- the momentum engine's own ranking (WORKLIST §31)
  2  RISING 4: of the top 20, above the 50-day average AND up over the month
  3  MONEY-FLOW 4: of the top 50, money flowing in (3+ of 4 checks in
     scanner.features.money_flow: sector gaining dollar-volume share, dollar
     volume surge, up-day volume, OBV rising)
     Both ranked by 12-1, skipping names another book holds (picks would wait)
  4  where the money moved: each sector's share of dollar volume, 5 vs 20 days
  5  track record: both lists graded forward against the S&P at 5 and 21
     sessions (scanner.ops.leaders_grade), from data/leaders/picks.jsonl

Steps 2-3 DESCRIBE, they are not tested rules: momentum crash guards failed,
trend-break exits lost to stop-only (§64), accumulation and insider flow
failed (TRIALS #5, #43). The grading is how either list earns trust.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings                               # noqa: E402
from scanner.execution import manual, ownership, picks            # noqa: E402
from scanner.execution.alpaca import Alpaca, BrokerError          # noqa: E402
from scanner.execution.policy import TradingPolicy                # noqa: E402
from scanner.execution.sizing import Sizing, size_position        # noqa: E402
from scanner.features import money_flow as mf                     # noqa: E402
from scanner.features.bars import fetch_bars, fetch_many          # noqa: E402
from scanner.features.intraday import EXCHANGE_TZ                 # noqa: E402
from scanner.features.intraday_context import atr_pct             # noqa: E402
from scanner.features.technicals import compute                   # noqa: E402
from scanner.momentum import signal                               # noqa: E402
from scanner.ops import leaders_grade as lg                       # noqa: E402
from scanner.sources import calendar as cal                       # noqa: E402
from scanner.sources import composite                             # noqa: E402
from scanner.sources.index_history import SP400, SP500            # noqa: E402

OUT = Path("logs/leaders")
RECORD = Path("data/leaders/picks.jsonl")
BENCHMARK = "^GSPC"
TOP_N = 20
FLOW_POOL = 50
PICK_N = 4
SMA_DAYS = 50
MONTH = 21
WEEK = 5
EARNINGS_DAYS = 7
POLICY = TradingPolicy()


@dataclass(frozen=True)
class Row:
    symbol: str
    sector: str
    mom: float
    r1m: float | None
    r5d: float | None
    above_sma: bool
    close: float


def ret(closes, n: int) -> float | None:
    return (closes[-1] / closes[-n - 1] - 1) * 100 if len(closes) > n else None


def make_row(symbol: str, sector: str, mom: float, closes) -> Row:
    sma = sum(closes[-SMA_DAYS:]) / min(len(closes), SMA_DAYS)
    return Row(symbol, sector, mom, ret(closes, MONTH), ret(closes, WEEK),
               closes[-1] > sma, closes[-1])


@dataclass(frozen=True)
class Prices:
    """The dollar side of a row: levels to compare the price against."""
    sma50: float
    high_52w: float
    dollar_volume: float          # mean close x volume, last 20 sessions
    atr_pct: float | None         # daily ATR % -- the same measure picks_sync stops on


def make_prices(bars) -> Prices:
    cl = bars.closes
    dv = sum(c * v for c, v in zip(cl[-20:], bars.volumes[-20:])) / min(len(cl), 20)
    return Prices(sma50=sum(cl[-SMA_DAYS:]) / min(len(cl), SMA_DAYS),
                  high_52w=max(bars.highs[-252:]), dollar_volume=dv, atr_pct=atr_pct(bars))


@dataclass(frozen=True)
class Estimate:
    limit: float
    stop: float
    sizing: Sizing


def estimate(close: float, atr: float | None, sleeve: float, room: float,
             policy: TradingPolicy = POLICY) -> Estimate | None:
    """What picks_sync would send for this name at `close`, with its own
    functions: limit = last + offset, stop = 2.5 daily ATRs under the limit,
    sized at the picks risk and per-position cap within the room left. The
    real order prices off the live quote at the first run, so this is close,
    not exact. None if it cannot be priced."""
    if not atr or sleeve <= 0:
        return None
    try:
        limit, stop, _ = picks.levels(last=close, atr_pct=atr,
                                      atr_multiple=policy.picks_stop_atr_multiple,
                                      offset_pct=policy.entry_limit_offset_pct,
                                      ceiling=policy.picks_target_ceiling)
    except ValueError:
        return None
    cap = min(policy.max_position_pct, policy.shared_position_cap_pct)
    return Estimate(limit, stop, size_position(sleeve, limit, stop, risk_pct=policy.risk_pct,
                                               max_position_pct=cap, buying_power=room))


def is_rising(row: Row) -> bool:
    return row.above_sma and row.r1m is not None and row.r1m > 0


def select(rows: list[Row], held_elsewhere: dict[str, str], keep,
           n: int = PICK_N) -> tuple[list[Row], list[Row]]:
    """(chosen, skipped): the first n rows passing `keep`, by 12-1, that no
    other book holds, and the passing rows skipped because another book does."""
    passing = sorted((r for r in rows if keep(r)), key=lambda r: -r.mom)
    skipped = [r for r in passing if r.symbol in held_elsewhere]
    chosen = [r for r in passing if r.symbol not in held_elsewhere][:n]
    return chosen, skipped


def completed(bars, today):
    """Drop a bar dated today: before or during the session it is still forming."""
    if bars.dates and bars.dates[-1] >= today:
        return bars.upto(len(bars) - 2)
    return bars


def as_triples(bars) -> list[tuple]:
    """scanner Bars -> selection.Bars: (day, open, close)."""
    return list(zip(bars.dates, bars.opens, bars.closes))


def load_records(path: Path = RECORD) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec.get("lists"), dict):
            out.append(rec)
    return out


@dataclass(frozen=True)
class Account:
    held: dict[str, str]          # {symbol: book} not owned by picks or manual
    sleeve: float                 # what picks + manual are sized against
    room: float                   # that sleeve minus what picks + manual hold


NO_ACCOUNT = Account(held={}, sleeve=0.0, room=0.0)


async def account_view(policy: TradingPolicy = POLICY) -> Account:
    """Read-only GETs; the sleeve and room as picks_sync computes them."""
    if not settings.alpaca_key_id:
        return NO_ACCOUNT
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker.account()
        positions = await broker._get("/v2/positions")
        history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
    owner_map = ownership.owners(history)
    mine = {ownership.PICKS, ownership.MANUAL}
    held = {p["symbol"]: ownership.owner(p["symbol"], owner_map) for p in positions
            if ownership.owner(p["symbol"], owner_map) not in mine}
    sleeve = manual.sleeve_equity(account.equity, policy.own_sleeves_pct)
    deployed = sum(float(p.get("market_value") or 0) for p in positions
                   if ownership.owner(p["symbol"], owner_map) not in ownership.OWN_SLEEVE)
    return Account(held=held, sleeve=sleeve,
                   room=max(0.0, min(account.buying_power, sleeve - deployed)))


@dataclass(frozen=True)
class Scan:
    rows: list[Row]                       # momentum top FLOW_POOL, best first
    prices: dict[str, Prices]
    flows: dict[str, mf.StockFlow]
    sectors: dict[str, mf.SectorFlow]
    earnings: dict
    failed: tuple
    as_of: object
    priced: int
    errors: int
    grades: list[lg.Grade]
    graded_days: int


def _inflow(sectors: dict[str, mf.SectorFlow], name: str | None) -> bool:
    flow = sectors.get(name) if name else None
    return bool(flow and flow.inflow)


async def gather(today, records) -> Scan:
    async with httpx.AsyncClient(timeout=120) as client:
        members = await composite.load_universe(client, (SP500, SP400))
        sector = {m.symbol: m.sector for m in members}
        daily, errors = await fetch_many(tuple(sorted(sector)), client, range_="2y")
        daily = {s: completed(b, today) for s, b in daily.items()}
        scores = {s: compute(b).momentum_12_1 for s, b in daily.items()}
        pool = signal.rank(scores, top_n=FLOW_POOL)
        rows = [make_row(s, sector.get(s, "?"), scores[s], daily[s].closes) for s in pool]
        prices = {s: make_prices(daily[s]) for s in pool}
        sectors = mf.sector_flows(daily, sector)
        flows = {s: mf.stock_flow(s, daily[s], _inflow(sectors, sector.get(s))) for s in pool}
        try:
            earnings, failed = await cal.upcoming_earnings(
                frozenset(pool), client, horizon=EARNINGS_DAYS)
        except Exception as exc:                                  # noqa: BLE001
            print(f"earnings calendar unavailable: {exc}")
            earnings, failed = {}, ("all",)
        grades: list[lg.Grade] = []
        if records:
            missing = tuple(s for s in lg.symbols_needed(records) if s not in daily)
            extra, _ = await fetch_many(missing, client, range_="2y") if missing else ({}, ())
            bench = completed(await fetch_bars(BENCHMARK, client, range_="2y"), today)
            history = {s: as_triples(b) for s, b in {**daily, **extra}.items()}
            grades = [lg.grade(records, history, as_triples(bench), h) for h in lg.HORIZONS]
    as_of = max(b.dates[-1] for b in daily.values() if b.dates)
    return Scan(rows, prices, flows, sectors, earnings, failed, as_of, len(daily), len(errors),
                grades, len(records))


def flow_cell(f: mf.StockFlow) -> str:
    marks = "".join(ch if ok else "·" for ch, ok in (
        ("S", f.sector_inflow),
        ("D", f.dollar_surge is not None and f.dollar_surge >= mf.SURGE),
        ("U", f.up_volume_share is not None and f.up_volume_share > 0.5),
        ("O", f.obv_rising)))
    return f"{f.points}/4 {marks}"


def money(v: float) -> str:
    """$1,234.56 under $1,000; $12.3K / $45.6M / $7.8B above."""
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return f"${v / size:,.1f}{unit}"
    return f"${v:,.2f}"


def level(value: float, price: float) -> str:
    """A price level and where the price sits against it: $154.93 (+40.7%)."""
    return f"${value:,.2f} ({(price / value - 1) * 100:+.1f}%)" if value else "?"


def change(price: float, pct_move: float | None) -> str:
    """A % move with the dollars it was per share: +37.9% (+$59.89)."""
    if pct_move is None:
        return "?"
    dollars = price - price / (1 + pct_move / 100)
    sign = "+" if dollars >= 0 else "-"
    return f"{pct_move:+.1f}% ({sign}${abs(dollars):,.2f})"


def _buy_cell(e: Estimate | None, sleeve: float) -> str:
    if e is None:
        return "?"
    if not e.sizing.is_tradeable:
        return f"none: {e.sizing.limited_by}"
    size, risk = e.sizing.position_dollars, e.sizing.risk_dollars
    return (f"{e.sizing.shares} sh @ ${e.limit:,.2f} = {money(size)} "
            f"({size / sleeve * 100:.1f}% of sleeve), risk {money(risk)} ({risk / sleeve * 100:.2f}%)")


def _list_table(chosen: list[Row], scan: Scan, account: Account, flags) -> list[str]:
    lines = ["| # | Stock | Price | 50-day avg | 52w high | 12-1 | 1 month | 5 days | Flow | "
             "$ traded/day | Stop (2.5 ATR) | picks would buy | Notes |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, r in enumerate(chosen, 1):
        f, p = scan.flows[r.symbol], scan.prices[r.symbol]
        surge = f" ({f.dollar_surge:.2f}x)" if f.dollar_surge is not None else ""
        e = estimate(r.close, p.atr_pct, account.sleeve, account.room)
        stop = f"${e.stop:,.2f} ({(e.stop / e.limit - 1) * 100:+.1f}%)" if e else "?"
        lines.append(f"| {n} | **{r.symbol}** | ${r.close:,.2f} | {level(p.sma50, r.close)} | "
                     f"{level(p.high_52w, r.close)} | {r.mom:+.0f}% | {change(r.close, r.r1m)} | "
                     f"{change(r.close, r.r5d)} | {flow_cell(f)} | {money(p.dollar_volume)}{surge} | "
                     f"{stop} | {_buy_cell(e, account.sleeve)} | {flags(r)} |")
    return lines


def _section(title: str, chosen, skipped, account: Account, scan, flags, pool: int) -> list[str]:
    held = account.held
    lines = [f"## {title}", ""]
    if chosen:
        lines += _list_table(chosen, scan, account, flags)
        if len(chosen) < PICK_N:
            lines += ["", f"Only {len(chosen)} of the top {pool} qualify today."]
    else:
        lines.append(f"None of the top {pool} qualify today.")
    if skipped:
        lines += ["", "Qualify but held by another book (picks would wait): "
                  + ", ".join(f"{r.symbol} ({held[r.symbol]})" for r in skipped)]
    return lines + [""]


def _sector_table(sectors: dict[str, mf.SectorFlow]) -> list[str]:
    lines = ["## Where the money moved (share of S&P 500 + 400 dollar volume, 5 vs 20 sessions)", "",
             "| Sector | $ traded/day, 5d | $ traded/day, 20d | Share 5d | Share 20d | Change | "
             "Median 5d return | |",
             "|---|---|---|---|---|---|---|---|"]
    for s in sorted(sectors.values(), key=lambda x: -x.share_change):
        verdict = "inflow" if s.inflow else ("outflow" if s.share_change < 0 and s.median_5d < 0 else "mixed")
        moved = (s.dollars_5d / s.dollars_20d - 1) * 100 if s.dollars_20d else 0.0
        lines.append(f"| {s.sector[:28]} | {money(s.dollars_5d)} ({moved:+.1f}%) | {money(s.dollars_20d)} | "
                     f"{s.share_5:.1f}% | {s.share_20:.1f}% | "
                     f"{s.share_change:+.1f}% | {s.median_5d:+.1f}% | {verdict} |")
    return lines + [""]


def _grade_lines(scan: Scan) -> list[str]:
    lines = ["## Track record (graded from the open of each report day, vs the S&P 500)", ""]
    if not scan.grades or all(g.excess["rising"].days == 0 and g.excess["flow"].days == 0
                              for g in scan.grades):
        return lines + [f"{scan.graded_days} report day(s) recorded; the first grade comes "
                        f"after {lg.HORIZONS[0]} sessions.", ""]
    lines += ["| Horizon | Rising 4 excess | Money-flow 4 excess | Flow minus rising | Days |",
              "|---|---|---|---|---|"]
    for g in scan.grades:
        r, f, e = g.excess["rising"], g.excess["flow"], g.flow_edge
        lines.append(f"| {g.horizon} sessions | {r.mean:+.2f}pp (t {r.t:.1f}) | "
                     f"{f.mean:+.2f}pp (t {f.t:.1f}) | {e.mean:+.2f}pp ({e.positive_days}/{e.days} days ahead) | "
                     f"{max(r.days, f.days)} |")
    return lines + ["", "_Horizons overlap day to day, so t overstates certainty; "
                        "orientation only._", ""]


def render(today, scan: Scan, rising, flow, account: Account, broker_note) -> str:
    held = account.held

    def flags(r: Row) -> str:
        out = []
        if r.symbol in scan.earnings:
            out.append(f"earnings {scan.earnings[r.symbol].label}")
        if r.symbol in held:
            out.append(f"held by {held[r.symbol]}")
        return "; ".join(out) or "-"

    lines = [f"# Morning leaders {today:%A %Y-%m-%d}", "",
             f"Closes up to {scan.as_of}; {scan.priced} stocks ranked (S&P 500 + MidCap 400). "
             "Report only: nothing here trades, you decide picks.txt.", "",
             "Flow checks: **S** sector gaining dollar-volume share, **D** dollar volume "
             f"5d >= {mf.SURGE}x the 20 before, **U** up-day volume > half of 10 days, "
             "**O** OBV rising over 10 days. Flowing = 3+.", "",
             "Reading the numbers: 50-day avg and 52w high show the level, then where the "
             "price sits against it (+ above, - below). Moves show % and the dollars per "
             "share. Stop shows the price and its distance below the limit.", ""]
    if account.sleeve:
        lines += [f"Prices are the last close. 'picks would buy' uses picks_sync's own sizing: "
                  f"picks sleeve {money(account.sleeve)}, room {money(account.room)}, "
                  f"limit +{POLICY.entry_limit_offset_pct}%; the real order prices "
                  "off the live quote at the first run.", ""]
    lines += _section(f"Rising 4 (top {TOP_N} by 12-1, above the 50-day, up over 1 month)",
                      *rising, account, scan, flags, TOP_N)
    lines += _section(f"Money-flow 4 (top {FLOW_POOL} by 12-1, money flowing in)",
                      *flow, account, scan, flags, FLOW_POOL)
    lines += ["Both lists DESCRIBE; neither is a tested rule. The track record below is "
              "how either earns trust.", ""]
    lines += _sector_table(scan.sectors)
    lines += _grade_lines(scan)
    lines += [f"## The momentum top {TOP_N}", "",
              "| # | Stock | Price | 50-day avg | 52w high | 12-1 | 1 month | 5 days | "
              "Flow | $ traded/day | Notes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {n} | {r.symbol} | ${r.close:,.2f} | {level(scan.prices[r.symbol].sma50, r.close)} | "
              f"{level(scan.prices[r.symbol].high_52w, r.close)} | {r.mom:+.0f}% | "
              f"{change(r.close, r.r1m)} | {change(r.close, r.r5d)} | {flow_cell(scan.flows[r.symbol])} | "
              f"{money(scan.prices[r.symbol].dollar_volume)} | {flags(r)} |"
              for n, r in enumerate(scan.rows[:TOP_N], 1)]
    notes = []
    if scan.failed:
        notes.append(f"earnings calendar could not be read for: {', '.join(map(str, scan.failed))}")
    if broker_note:
        notes.append(broker_note)
    if notes:
        lines += ["", *(f"_{n}_" for n in notes)]
    return "\n".join(lines) + "\n"


def record(today, scan: Scan, rising, flow) -> dict:
    def entry(rows):
        return [r.symbol for r in rows]
    return {"day": today.isoformat(), "as_of": str(scan.as_of),
            "lists": {"rising": entry(rising[0]), "flow": entry(flow[0])},
            "closes": {r.symbol: r.close for r in rising[0] + flow[0]},
            "flow_points": {r.symbol: scan.flows[r.symbol].points for r in rising[0] + flow[0]},
            "held_elsewhere": sorted({r.symbol for r in rising[1] + flow[1]})}


async def main(write: bool) -> int:
    today = datetime.now(tz=EXCHANGE_TZ).date()
    records = [r for r in load_records() if r["day"] != today.isoformat()]
    scan = await gather(today, records)
    broker_note = ""
    try:
        account = await account_view()
    except (BrokerError, httpx.HTTPError) as exc:
        account = NO_ACCOUNT
        broker_note = f"broker unreachable, holdings and sizing not checked: {exc}"
    rising = select(scan.rows[:TOP_N], account.held, is_rising)
    flow = select(scan.rows, account.held, lambda r: scan.flows[r.symbol].flowing)
    text = render(today, scan, rising, flow, account, broker_note)
    print(text)
    if scan.errors:
        print(f"{scan.errors} symbols failed to price")
    if not write:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{today.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    # One record per day: a re-run replaces that day's line instead of adding a second.
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + [json.dumps(record(today, scan, rising, flow))]
    tmp = RECORD.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(RECORD)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--no-write", action="store_true", help="print only")
    raise SystemExit(asyncio.run(main(not p.parse_args().no_write)))
