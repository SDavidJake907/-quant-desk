"""Dividend history — yield, growth streaks, and the monthly-payer trap."""

from __future__ import annotations

from datetime import date

from scanner.sources.dividend_history import (
    DividendHistory,
    Payment,
    parse,
)


def _history(symbol, price, schedule):
    """schedule: {year: [amount, ...]} paid on evenly spaced dates."""
    payments = []
    for year, amounts in schedule.items():
        step = max(1, 12 // len(amounts))
        for n, amount in enumerate(amounts):
            payments.append(Payment(paid_on=date(year, min(12, 1 + n * step), 15),
                                    amount=amount))
    return DividendHistory(symbol=symbol, price=price,
                           payments=tuple(sorted(payments, key=lambda p: p.paid_on)))


# --- the monthly-payer trap ----------------------------------------------

def test_trailing_yield_uses_a_date_window_not_a_payment_count():
    """Realty Income pays monthly. "Last four payments" is a third of a year.

    Under the naive method it reports ~1.7% against a true yield near 5.6% —
    an error that understates the best income names threefold and would steer
    a screen away from exactly what it is looking for.
    """
    monthly = _history("O", 62.36, {2025: [0.29] * 12, 2026: [0.29] * 12})
    at = date(2026, 12, 31)
    assert round(monthly.trailing_12m(at), 2) == 3.48       # twelve payments
    assert 5.4 < monthly.yield_pct(at) < 5.7

    naive = sum(p.amount for p in monthly.payments[-4:])     # the wrong method
    assert round(naive / 62.36 * 100, 1) == 1.9


def test_quarterly_and_monthly_payers_yield_comparably():
    at = date(2026, 12, 31)
    q = _history("KO", 100.0, {2025: [1.0] * 4, 2026: [1.0] * 4})
    m = _history("O", 100.0, {2025: [1 / 3] * 12, 2026: [1 / 3] * 12})
    assert round(q.yield_pct(at)) == round(m.yield_pct(at)) == 4


def test_frequency_is_detected():
    assert _history("O", 60.0, {y: [0.29] * 12 for y in (2023, 2024, 2025, 2026)}).frequency() == "monthly"
    assert _history("KO", 86.0, {y: [0.5] * 4 for y in (2023, 2024, 2025, 2026)}).frequency() == "quarterly"
    assert _history("X", 10.0, {y: [1.0] for y in (2023, 2024, 2025, 2026)}).frequency() == "annual"


# --- growth and cuts ------------------------------------------------------

def test_growth_streak_counts_consecutive_raises():
    mo = _history("MO", 67.75, {2021: [0.86] * 4, 2022: [0.92] * 4,
                                2023: [0.96] * 4, 2024: [1.00] * 4,
                                2025: [1.04] * 4, 2026: [1.06] * 4})
    assert mo.complete_years() and mo.growth_streak() == 3   # 2022->23->24->25
    assert mo.was_cut() is False
    assert 3.0 < mo.growth_pct() < 5.5


def test_a_cut_ends_the_streak_and_is_flagged():
    """AT&T: paid 1.35, cut to 1.11, then froze. High yield, broken record."""
    t = _history("T", 23.71, {2021: [0.52] * 4, 2022: [0.3375] * 4,
                              2023: [0.2775] * 4, 2024: [0.2775] * 4,
                              2025: [0.2775] * 4, 2026: [0.2775] * 4})
    assert t.was_cut() is True
    assert t.growth_streak() == 0


def test_a_frozen_payout_is_not_a_raise():
    flat = _history("X", 50.0, {y: [1.0] * 4 for y in range(2021, 2027)})
    assert flat.growth_streak() == 0
    assert flat.was_cut() is False


def test_partial_first_and_last_years_are_excluded():
    """A 5y window opens mid-year and the current year is always incomplete.

    Comparing a partial year to a full one invents a cut, which on a screen
    that rejects cutters would discard the best names for a calendar artifact.
    """
    partial = _history("KO", 86.0, {2022: [0.44],            # partial: 1 payment
                                    2023: [0.46] * 4,
                                    2024: [0.485] * 4,
                                    2025: [0.51] * 4,
                                    2026: [0.53]})           # partial: 1 payment
    assert sorted(partial.complete_years()) == [2023, 2024, 2025]
    assert partial.was_cut() is False
    assert partial.growth_streak() == 2


def test_too_little_history_yields_no_complete_years():
    thin = _history("NEW", 20.0, {2025: [0.1], 2026: [0.1]})
    assert thin.complete_years() == {}
    assert thin.growth_streak() == 0
    assert thin.was_cut() is False
    assert thin.frequency() == "none"


# --- parsing --------------------------------------------------------------

def _payload(price, divs):
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": price},
        "events": {"dividends": {str(ts): {"date": ts, "amount": a}
                                 for ts, a in divs}},
    }]}}


def test_parse_reads_price_and_payments():
    h = parse("KO", _payload(86.85, [(1735689600, 0.51), (1743465600, 0.53)]))
    assert h.symbol == "KO" and h.price == 86.85
    assert len(h.payments) == 2
    assert h.payments[0].paid_on < h.payments[1].paid_on


def test_parse_drops_bad_entries_but_keeps_good_ones():
    h = parse("X", _payload(10.0, [(1735689600, 0.5), (1743465600, 0),
                                   ("bad", 1.0), (1751328000, -1.0)]))
    assert [p.amount for p in h.payments] == [0.5]


def test_parse_survives_junk():
    assert parse("X", None) is None
    assert parse("X", {}) is None
    assert parse("X", {"chart": {"result": []}}) is None
    assert parse("X", {"chart": {"result": [{"meta": {}}]}}) is None


def test_a_non_payer_is_not_an_error():
    h = parse("NVDA", _payload(220.0, []))
    assert h is not None
    assert h.pays_dividends is False
    assert h.yield_pct() == 0.0


def test_payment_count_drift_does_not_invent_a_cut():
    """The Realty Income bug.

    A monthly payer does not reliably pay twelve times per calendar year — a
    December declaration can settle in January. O paid 11 times in 2024 and 13
    in 2025, so raw totals read 3.06 -> 2.87 -> 3.49 and the screen called a
    25-year dividend raiser a CUTTER and rejected it. Per-payment rates over
    the same span rise monotonically.
    """
    o = DividendHistory(symbol="O", price=62.36, payments=tuple(
        [Payment(paid_on=date(2022, m, 15), amount=0.2477) for m in range(1, 13)]
        + [Payment(paid_on=date(2023, m, 15), amount=0.2552) for m in range(1, 13)]
        + [Payment(paid_on=date(2024, m, 15), amount=0.2611) for m in range(2, 13)]
        + [Payment(paid_on=date(2025, m, 15), amount=0.2685) for m in range(1, 13)]
        + [Payment(paid_on=date(2025, 12, 31), amount=0.2685)]
        + [Payment(paid_on=date(2026, m, 15), amount=0.2700) for m in range(1, 4)]
    ))
    assert o.was_cut() is False
    assert o.growth_streak() == 2      # 2023 -> 2024 -> 2025


def test_normalisation_does_not_mask_a_real_cut():
    """AT&T cut the RATE, not just the count. Normalising must still see it.

    The cut is placed mid-window on purpose: the first year of any window is
    dropped as partial, so a cut in that year is invisible. That is a real
    limitation of the method, not a bug — and it is the reason `fetch`
    defaults to ten years rather than five.
    """
    t = DividendHistory(symbol="T", price=23.71, payments=tuple(
        [Payment(paid_on=date(2021, m, 15), amount=0.52) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2022, m, 15), amount=0.52) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2023, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2024, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2025, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2026, 1, 15), amount=0.2775)]
    ))
    assert t.was_cut() is True
    assert t.growth_streak() == 0


def test_a_cut_in_the_dropped_first_year_is_not_visible():
    """Documents the limitation above rather than pretending it away."""
    t = DividendHistory(symbol="T", price=23.71, payments=tuple(
        [Payment(paid_on=date(2022, m, 15), amount=0.52) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2023, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2024, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
        + [Payment(paid_on=date(2025, m, 15), amount=0.2775) for m in (1, 4, 7, 10)]
    ))
    assert sorted(t.complete_years()) == [2023, 2024]
    assert t.was_cut() is False      # the 2022 -> 2023 cut is outside the window
