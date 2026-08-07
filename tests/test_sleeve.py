"""The sleeve boundary — the arithmetic that keeps two pots of money apart."""

from __future__ import annotations

from scanner.execution.sizing import size_position
from scanner.execution.sleeve import (
    SWING_PREFIX,
    is_swing_position,
    split,
)


def test_default_allocation_changes_nothing():
    """Adding sleeves must not alter behaviour until an allocation is set."""
    sleeves = split(100_000.0)
    assert sleeves.swing_equity == 100_000.0
    assert sleeves.income_equity == 0.0


def test_allocation_splits_equity():
    sleeves = split(90_000.0, swing_allocation_pct=66.667)
    assert round(sleeves.swing_equity) == 60_000
    assert round(sleeves.income_equity) == 30_000


def test_only_scanner_positions_count_as_swing():
    """A manual buy must not consume the strategy's capital.

    This is the NVDA case: a position the user opened by hand sat in the same
    account and was briefly counted as the strategy's, producing nonsense
    slippage figures. Here the same mistake would misreport how much swing
    capital is free.
    """
    sleeves = split(
        100_000.0,
        swing_allocation_pct=50.0,
        positions=(
            (f"{SWING_PREFIX}AMD-1786035600", 19_762.0),   # ours
            (None, 875.0),                                  # manual NVDA
            ("income-KO-2026Q3", 12_000.0),                 # income sleeve
        ),
    )
    assert sleeves.swing_deployed == 19_762.0
    assert sleeves.untracked_deployed == 12_875.0
    assert sleeves.swing_available == 50_000.0 - 19_762.0


def test_unknown_order_ids_are_not_ours():
    assert is_swing_position(f"{SWING_PREFIX}AMD-1") is True
    assert is_swing_position("income-KO-1") is False
    assert is_swing_position("") is False
    assert is_swing_position(None) is False


def test_available_never_goes_negative():
    """Lowering the allocation under open trades must not produce a negative
    cap that would flow into sizing as nonsense."""
    sleeves = split(
        100_000.0,
        swing_allocation_pct=10.0,
        positions=((f"{SWING_PREFIX}AMD-1", 40_000.0),),
    )
    assert sleeves.over_deployed is True
    assert sleeves.swing_available == 0.0


def test_the_bug_this_module_exists_to_prevent():
    """Sizing off account equity instead of sleeve equity inflates real risk.

    With a third of the account in dividend holdings, risking "1% of equity"
    against the full balance risks 1.5% of the capital the strategy actually
    has — and the position cap inflates by the same factor. Nothing errors;
    the account simply becomes more concentrated than was chosen.
    """
    account_equity = 99_000.0
    entry, stop = 494.06, 485.55

    naive = size_position(account_equity, entry, stop,
                          risk_pct=1.0, max_position_pct=20.0)
    sleeves = split(account_equity, swing_allocation_pct=66.667)
    correct = size_position(sleeves.swing_equity, entry, stop,
                            risk_pct=1.0, max_position_pct=20.0)

    assert correct.shares < naive.shares
    # The naive size risks half again as much of the money actually available.
    naive_risk_of_sleeve = naive.risk_dollars / sleeves.swing_equity * 100.0
    correct_risk_of_sleeve = correct.risk_dollars / sleeves.swing_equity * 100.0
    assert naive_risk_of_sleeve > correct_risk_of_sleeve * 1.4
    assert correct.position_dollars <= sleeves.swing_equity * 0.20 + entry


def test_utilisation_reports_sleeve_not_account():
    sleeves = split(
        100_000.0,
        swing_allocation_pct=50.0,
        positions=((f"{SWING_PREFIX}AMD-1", 25_000.0),),
    )
    assert sleeves.swing_utilisation == 50.0  # half the sleeve, a quarter of the account


def test_zero_and_negative_equity_are_safe():
    assert split(0.0).swing_equity == 0.0
    assert split(-5_000.0).swing_equity == 0.0
    assert split(0.0, positions=((f"{SWING_PREFIX}X-1", 10.0),)).swing_utilisation == 0.0


def test_allocation_is_clamped():
    assert split(1_000.0, swing_allocation_pct=150.0).swing_equity == 1_000.0
    assert split(1_000.0, swing_allocation_pct=-10.0).swing_equity == 0.0


def test_a_percentage_sleeve_does_not_move_profit_to_income():
    """The reason `swing_cap` exists.

    A percentage recomputes off the new total, so profit stays where it was
    made. Intending "profits accumulate into dividend holdings" and expressing
    it as a fixed percentage silently does nothing.
    """
    before = split(99_000.0, swing_allocation_pct=66.7)
    after = split(150_000.0, swing_allocation_pct=66.7)
    assert after.swing_equity > before.swing_equity      # swing grew too
    grew = after.income_equity / before.income_equity
    assert 1.5 < grew < 1.6                              # merely in proportion


def test_a_capped_sleeve_sends_profit_to_income():
    before = split(99_000.0, swing_allocation_pct=66.7, swing_cap=66_000.0)
    after = split(150_000.0, swing_allocation_pct=66.7, swing_cap=66_000.0)
    assert after.swing_equity == before.swing_equity == 66_000.0
    assert after.income_equity - before.income_equity == 51_000.0


def test_the_cap_binds_over_the_percentage():
    """A ceiling a percentage could raise is not a ceiling."""
    s = split(500_000.0, swing_allocation_pct=100.0, swing_cap=66_000.0)
    assert s.swing_equity == 66_000.0
    assert s.income_equity == 434_000.0


def test_cap_above_the_percentage_does_not_raise_the_sleeve():
    s = split(99_000.0, swing_allocation_pct=50.0, swing_cap=90_000.0)
    assert s.swing_equity == 49_500.0


def test_negative_cap_is_clamped_not_inverted():
    s = split(99_000.0, swing_cap=-5_000.0)
    assert s.swing_equity == 0.0
    assert s.income_equity == 99_000.0
