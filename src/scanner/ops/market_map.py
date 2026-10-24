"""The market map: where money moved in the S&P 500 today (operator 2026-10-23: "how were you able to
view all the stocks that crash and the one that went up? can we give gemma a view like that?").

Every member's % move x its market value = the dollars it gained or lost; summed by sector, ranked
by dollars, with breadth (how many rose) and a ROTATION read when the index falls while most stocks
rise (10-22: -$1.1T while 338 of 495 rose). Live prices from Alpaca snapshots (IEX: the direction
is right, volumes read low); after the close the snapshot is the finished day.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

import httpx

from scanner.models import Frozen
from scanner.sources import alpaca_bars, composite, market_caps
from scanner.sources.index_history import SP500

TOP = 8


class Row(Frozen):
    symbol: str
    sector: str
    pct: float
    cap: float
    vol_ratio: float | None = None

    @property
    def dollars(self) -> float:
        return self.cap * self.pct / 100


class Map(Frozen):
    rows: tuple[Row, ...]

    @property
    def total_dollars(self) -> float:
        return sum(r.dollars for r in self.rows)

    @property
    def total_pct(self) -> float:
        cap = sum(r.cap for r in self.rows)
        return self.total_dollars / cap * 100 if cap else 0.0

    @property
    def up(self) -> int:
        return sum(r.pct > 0 for r in self.rows)

    def sectors(self) -> list[tuple[str, float, float, int]]:
        """(sector, dollars, % of its value, members), worst first."""
        d: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        for r in self.rows:
            d[r.sector][0] += r.dollars
            d[r.sector][1] += r.cap
            d[r.sector][2] += 1
        return sorted(((s, v[0], v[0] / v[1] * 100 if v[1] else 0.0, int(v[2])) for s, v in d.items()),
                      key=lambda x: x[1])

    def rotation(self) -> bool:
        return self.total_pct <= -0.5 and self.up >= 0.55 * len(self.rows)

    def broad(self) -> bool:
        return self.total_pct <= -1.0 and self.up <= 0.25 * len(self.rows)


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x) / 1e9:,.0f}B"


def _row(r: Row) -> str:
    vol = f", volume {r.vol_ratio:.1f}x yesterday" if r.vol_ratio else ""
    return f"{r.symbol} {r.pct:+.1f}% ({_money(r.dollars)}{vol})"


def text(m: Map, held: Iterable[str] = (), top: int = TOP) -> str:
    if not m.rows:
        return "Market map unavailable."
    n = len(m.rows)
    read = ("ROTATION, not a crash: the index is down while most stocks are up" if m.rotation() else
            "BROAD selling: most stocks are down" if m.broad() else "")
    lines = [f"MARKET MAP (S&P 500 members, today vs the prior close): {_money(m.total_dollars)} "
             f"({m.total_pct:+.2f}%); {m.up} of {n} stocks up." + (f" {read}." if read else "")]
    lines.append("By sector: " + "; ".join(f"{s} {_money(d)} ({p:+.1f}%)" for s, d, p, _ in m.sectors()))
    by = sorted(m.rows, key=lambda r: r.dollars)
    lines.append("Biggest dollar losers: " + "; ".join(_row(r) for r in by[:top]))
    lines.append("Biggest dollar gainers: " + "; ".join(_row(r) for r in reversed(by[-top:])))
    mine = {r.symbol: r for r in m.rows}
    held = [s for s in held if s in mine]
    if held:
        lines.append("Your holdings: " + "; ".join(f"{s} ({mine[s].sector}) {mine[s].pct:+.1f}%" for s in held))
    return "\n".join(lines)


def sector_text(m: Map, sector: str, top: int = TOP) -> str:
    rows = [r for r in m.rows if r.sector.lower() == sector.lower()]
    if not rows:
        names = sorted({r.sector for r in m.rows})
        return f"No sector named {sector!r}. Sectors: {', '.join(names)}"
    rows.sort(key=lambda r: r.pct)
    up = sum(r.pct > 0 for r in rows)
    return (f"{rows[0].sector}: {up} of {len(rows)} up. Worst: " + "; ".join(_row(r) for r in rows[:top])
            + ". Best: " + "; ".join(_row(r) for r in reversed(rows[-top:])))


async def fetch(client: httpx.AsyncClient) -> Map:
    """Today's map (or the last finished session's when the market is closed). Raises on failure."""
    members = await composite.load_universe(client, (SP500,))
    caps = await market_caps.load(client)
    sectors = {m.symbol: m.sector for m in members}
    snaps = await alpaca_bars.snapshots(sorted(sectors), client)
    rows = []
    for s, v in snaps.items():
        daily, prev = v.get("dailyBar") or {}, v.get("prevDailyBar") or {}
        last = (v.get("latestTrade") or {}).get("p") or daily.get("c")
        if not last or not prev.get("c") or not caps.get(s):
            continue
        ratio = (daily.get("v") / prev["v"]) if daily.get("v") and prev.get("v") else None
        rows.append(Row(symbol=s, sector=sectors.get(s, "unknown"), pct=(float(last) / float(prev["c"]) - 1) * 100,
                        cap=float(caps[s]), vol_ratio=ratio))
    return Map(rows=tuple(rows))
