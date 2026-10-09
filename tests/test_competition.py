"""Competition scoring: reproducible ghosts, honest stops, fair winners."""

from __future__ import annotations

from datetime import date

import pytest

from scanner.ops import competition as c

D = date


def test_quarters_and_months():
    assert c.month_of(D(2026, 10, 7)) == "2026-10"
    assert c.quarter_of("2026-10") == "2026-Q4" and c.quarter_of("2027-01") == "2027-Q1"


def test_ghost_draws_are_reproducible_and_sized():
    pool = [f"S{i}" for i in range(150)]
    a = c.draw(pool, 8, "2026-10", c.GHOST_PICKS)
    assert a == c.draw(list(reversed(pool)), 8, "2026-10", c.GHOST_PICKS)
    assert len(a) == 8 and len(set(a)) == 8
    assert a != c.draw(pool, 8, "2026-11", c.GHOST_PICKS)
    assert len(c.draw(["A", "B"], 5, "2026-10", c.GHOST_GEMMA)) == 2


def test_paper_stops_hit_on_daily_low_and_gaps_fill_at_open():
    book = c.PaperBook(name=c.GHOST_GEMMA, month="2026-10", start=D(2026, 10, 7), names=(
        c.PaperName(symbol="A", entry=100, stop=90),
        c.PaperName(symbol="B", entry=100, stop=90),
        c.PaperName(symbol="C", entry=100, stop=None)))
    bars = {"A": [(D(2026, 10, 7), 100, 80, 95),     # entry day never stops
                  (D(2026, 10, 8), 95, 89, 92)],     # low hits 90 -> exit at 90
            "B": [(D(2026, 10, 8), 85, 84, 86)],     # gapped below -> exit at the 85 open
            "C": [(D(2026, 10, 8), 100, 50, 110)]}   # no stop
    after = c.advance(book, bars)
    got = {n.symbol: (n.exit_day, n.exit_price) for n in after.names}
    assert got["A"] == (D(2026, 10, 8), 90) and got["B"] == (D(2026, 10, 8), 85)
    assert got["C"] == (None, None)
    r = c.paper_return(after, {"A": 120, "B": 120, "C": 110})
    assert r == pytest.approx(((90 / 100 - 1) + (85 / 100 - 1) + (110 / 100 - 1)) / 3 * 100)


def test_real_book_return_over_average_invested():
    snaps = [c.Snapshot(day=D(2026, 10, 7), book=c.PICKS, pl_total=0, invested=10_000),
             c.Snapshot(day=D(2026, 10, 8), book=c.PICKS, pl_total=150, invested=10_000),
             c.Snapshot(day=D(2026, 10, 9), book=c.PICKS, pl_total=300, invested=20_000)]
    assert c.book_return(snaps, c.PICKS, D(2026, 10, 7), D(2026, 10, 9)) == pytest.approx(300 / (40_000 / 3) * 100)
    assert c.book_return(snaps, c.GEMMA, D(2026, 10, 7), D(2026, 10, 9)) is None


def test_winners():
    assert c.month_winner({c.PICKS: 2.0, c.GEMMA: 1.0}) == c.PICKS
    assert c.month_winner({c.PICKS: 1.0, c.GEMMA: 1.0}) is None
    months = [c.MonthResult(month="2026-10", scores={c.PICKS: 3.0, c.GEMMA: 1.0}, winner=c.PICKS),
              c.MonthResult(month="2026-11", scores={c.PICKS: -1.0, c.GEMMA: 2.0}, winner=c.GEMMA)]
    assert c.quarter_winner(months) == c.GEMMA          # 1-1 in months; Gemma +3 vs +2 overall
    months.append(c.MonthResult(month="2026-12", scores={c.PICKS: 1.0, c.GEMMA: 0.0}, winner=c.PICKS))
    assert c.quarter_winner(months) == c.PICKS          # 2-1 in months


def test_daily_returns_and_probability_of_being_better():
    from datetime import timedelta
    snaps = [c.Snapshot(day=D(2026, 10, 7) + timedelta(days=i), book=c.PICKS, pl_total=10.0 * i,
                        invested=1000) for i in range(4)]
    r = c.daily_returns(snaps, c.PICKS)
    assert list(r.values()) == pytest.approx([1.0, 1.0, 1.0])
    days = [D(2026, 10, 1) + timedelta(days=i) for i in range(12)]
    strong = {d: 1.0 + (0.1 if i % 2 else -0.1) for i, d in enumerate(days)}
    weak = {d: 0.0 for d in days}
    assert c.prob_better(strong, weak) == pytest.approx(1.0)
    assert c.prob_better(weak, strong) == pytest.approx(0.0)
    assert c.prob_better({days[0]: 1.0}, {days[0]: 0.0}) is None      # too few days


def test_competition_prices_can_be_forced_fresh(monkeypatch):
    # The 13:30 scoring once reused prices cached that morning, so the ghosts,
    # momentum and S&P were a day behind the live books (2026-10-09).
    import asyncio
    from scanner.features import bars as fb
    seen = {}

    async def fake_payload(symbol, client, *, range_, interval, ttl_seconds=None):
        seen[symbol] = ttl_seconds
        raise RuntimeError("stop here")

    monkeypatch.setattr(fb, "fetch_payload", fake_payload)
    asyncio.run(fb.fetch_many(("AAA",), None, range_="3mo", ttl_seconds=3600))
    assert seen == {"AAA": 3600}
