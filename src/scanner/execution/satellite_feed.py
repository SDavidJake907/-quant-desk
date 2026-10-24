"""Fetches what the plan's buy rules (execution/satellite.py) need for Gemma's new buys:
the three gates, index membership and sectors, her pool's momentum, each name's daily
bars, earnings dates and her recent sales. Raises nothing it can avoid: a name whose
facts cannot be read is refused with that reason -- a buy needs every check."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

import httpx

from scanner.advisor import gemma_pool as gp
from scanner.execution import satellite as sat
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.models import Frozen
from scanner.sources import calendar as event_calendar
from scanner.sources import composite

INDEX_SEGMENT = {"S&P 500": "large", "S&P MidCap 400": "mid", "S&P SmallCap 600": "small"}
SEGMENT_LABEL = {"large": "big caps (gate 1, SPY)", "mid": "mid caps (gate 2, MDY)",
                 "small": "small caps (gate 3, IJR)"}


class Gates(Frozen):
    open: dict[str, bool | None]
    vs_average: dict[str, float | None]

    def text(self) -> str:
        parts = []
        for seg, etf in sat.GATES.items():
            pct = self.vs_average.get(seg)
            state = {True: "OPEN", False: "CLOSED", None: "UNKNOWN (no data: closed)"}[self.open.get(seg)]
            parts.append(f"{SEGMENT_LABEL[seg]} {state}" + (f" ({pct:+.1f}% vs its 200-day)" if pct is not None else ""))
        return "Your buy gates today: " + "; ".join(parts) + "."


async def gates(client: httpx.AsyncClient) -> Gates:
    is_open, vs = {}, {}
    for seg, etf in sat.GATES.items():
        try:
            closes = (await fetch_bars(etf, client, range_="2y")).closes
            is_open[seg] = sat.gate_open(closes)
            avg = sum(closes[-sat.GATE_DAYS:]) / sat.GATE_DAYS if len(closes) >= sat.GATE_DAYS else None
            vs[seg] = (closes[-1] / avg - 1) * 100 if avg else None
        except Exception:                                     # noqa: BLE001 -- unknown = closed
            is_open[seg], vs[seg] = None, None
    return Gates(open=is_open, vs_average=vs)


def sold_recently(rows: Iterable[dict], since: datetime) -> set[str]:
    """Names Gemma's book closed since `since` (audit gemma_exit events)."""
    out = set()
    for r in rows:
        if r.get("event") != "gemma_exit" or not r.get("symbol"):
            continue
        try:
            if datetime.fromisoformat(str(r["at"])) >= since:
                out.add(str(r["symbol"]))
        except ValueError:
            continue
    return out


async def refusals(client: httpx.AsyncClient, buys: Sequence[str], held: Iterable[str],
                   g: Gates, audit_path: str, today: date | None = None) -> dict[str, str]:
    """{symbol: why refused} for the new buys; a name absent from the result may be bought.
    Each accepted buy counts toward the sector cap for the next."""
    today = today or date.today()
    if not buys:
        return {}
    members = await composite.load_universe(client)
    segment = {m.symbol: INDEX_SEGMENT.get(m.index, "large") for m in members}
    sectors = {m.symbol: m.sector for m in members}
    pool = gp.latest(today)
    mom = {r.symbol: r.mom_12_1 for r in pool}
    sectors.update({r.symbol: r.sector for r in pool if r.symbol not in sectors})
    bars, _ = await fetch_many(tuple(buys), client, range_="1y")
    found, failed = await event_calendar.upcoming_earnings(
        frozenset(buys), client, horizon=sat.EARNINGS_BUFFER_DAYS + 3, today=today)
    sold = sold_recently(read_rows(audit_path), datetime.now().astimezone()
                         - timedelta(days=sat.REBUY_LOOKBACK_DAYS))
    held_sectors = sat.sector_counts(held, sectors)
    out: dict[str, str] = {}
    for s in buys:
        b = bars.get(s)
        if b is None or len(b.closes) < 130:
            out[s] = "no year of daily bars to check the rules"
            continue
        if failed:
            out[s] = "earnings calendar unreadable -- refused (fails closed)"
            continue
        c = b.closes
        seg = sat.segment_of(s, segment)
        e = found.get(s)
        why = sat.buy_refusal(
            gate=g.open.get(seg), gate_name=SEGMENT_LABEL[seg], top_momentum=sat.momentum_top(s, mom),
            ret_6m=(c[-1] / c[-127] - 1) * 100, above_50d=c[-1] > sum(c[-50:]) / 50,
            earnings_in_days=(e.on - today).days if e else None, sector=sectors.get(s, "unknown"),
            held_sectors=held_sectors, sold_recently=s in sold, new_20d_high=sat.new_closing_high(c))
        if why:
            out[s] = why
        else:
            sec = sectors.get(s, "unknown")
            held_sectors = {**held_sectors, sec: held_sectors.get(sec, 0) + 1}
    return out
