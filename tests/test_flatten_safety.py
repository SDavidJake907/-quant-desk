"""The Friday close-out, in the states where it actually has to work.

This is the only scheduled job that sends closing orders, and it runs
unattended fifteen minutes before a bell. Every failure mode below leaves a
position exposed over exactly the weekend the rule exists to avoid, and most of
them look like success in a log nobody opens until Monday.

The early-close case is the one that would have bitten. US markets close at
13:00 ET on the Friday after Thanksgiving and around July 4th. A task pinned to
15:45 ET fires two and three quarter hours AFTER the bell on those days, finds
the market shut, and cannot flatten anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scanner.execution.flatten import (
    FlattenPlan,
    closing_within,
    is_flat,
    plan_flatten,
    should_wind_down,
)

ET = timezone(timedelta(hours=-4))


class TestClosingWindow:
    """Read the session end from the broker calendar, never from a constant."""

    def test_a_normal_friday_close_is_recognised(self):
        now = datetime(2026, 8, 21, 15, 45, tzinfo=ET)
        assert closing_within(now, close_at=datetime(2026, 8, 21, 16, 0, tzinfo=ET),
                              minutes=20) is True

    def test_hours_before_the_close_it_declines_to_act(self):
        now = datetime(2026, 8, 21, 9, 45, tzinfo=ET)
        assert closing_within(now, close_at=datetime(2026, 8, 21, 16, 0, tzinfo=ET),
                              minutes=20) is False

    def test_an_early_close_is_caught_by_the_earlier_run(self):
        """13:00 ET close. The 08:45 AK trigger is 12:45 ET, fifteen minutes
        out, and must act — the 11:45 AK trigger would be nearly three hours
        after the bell."""
        now = datetime(2026, 11, 27, 12, 45, tzinfo=ET)
        close = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
        assert closing_within(now, close_at=close, minutes=20) is True

    def test_after_the_bell_is_not_a_closing_window(self):
        """Already shut is a different state from about to shut, and must not
        be mistaken for it — nothing can be flattened after the close."""
        now = datetime(2026, 11, 27, 15, 45, tzinfo=ET)
        close = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
        assert closing_within(now, close_at=close, minutes=20) is False

    def test_no_session_today_is_not_a_closing_window(self):
        """A market holiday has no close to be near."""
        assert closing_within(datetime(2026, 12, 25, 11, 0, tzinfo=ET),
                              close_at=None, minutes=20) is False


class TestIdempotence:
    """Running twice must never open a short."""

    def test_a_flat_account_produces_no_orders(self):
        plan = plan_flatten(positions=[], orders=[])
        assert plan.is_empty is True

    def test_a_second_run_after_success_is_a_no_op(self):
        first = plan_flatten(positions=[{"symbol": "AAPL", "qty": "10"}], orders=[])
        assert first.close == ("AAPL",)
        # The broker now reports nothing held; the plan must be empty, not a sell.
        second = plan_flatten(positions=[], orders=[])
        assert second.is_empty is True

    def test_quantities_come_from_the_broker_not_from_intent(self):
        """A partial fill leaves fewer shares than were ordered. The plan is
        built from what the broker says is held, so a stale local number can
        never drive a closing order."""
        plan = plan_flatten(positions=[{"symbol": "MU", "qty": "3"}], orders=[])
        assert plan.close == ("MU",)

    def test_a_short_position_is_still_closed_not_doubled(self):
        """Negative quantity is a short. Closing it buys back; it must appear
        exactly once and must not be skipped."""
        plan = plan_flatten(positions=[{"symbol": "TSLA", "qty": "-5"}], orders=[])
        assert plan.close == ("TSLA",)


class TestIsFlat:
    """The verification predicate, read back from the broker."""

    def test_no_positions_and_no_orders_is_flat(self):
        assert is_flat(positions=[], orders=[]) is True

    def test_a_remaining_position_is_not_flat(self):
        assert is_flat(positions=[{"symbol": "F", "qty": "2"}], orders=[]) is False

    def test_a_working_order_is_not_flat(self):
        """An unfilled entry left resting can fill on Monday into a book that
        is supposed to be empty."""
        assert is_flat(positions=[],
                       orders=[{"id": "x", "status": "new"}]) is False

    def test_a_zero_quantity_position_does_not_block_flatness(self):
        assert is_flat(positions=[{"symbol": "F", "qty": "0"}], orders=[]) is True

    def test_filled_and_cancelled_orders_do_not_block_flatness(self):
        assert is_flat(positions=[],
                       orders=[{"id": "a", "status": "filled"},
                               {"id": "b", "status": "canceled"}]) is True


class TestFridayEntriesAreBlockedIndependently:
    def test_friday_is_wound_down_even_if_the_flatten_job_never_runs(self):
        """The two rules must not depend on each other. A missed flatten must
        not also silently re-enable Friday entries."""
        assert should_wind_down(datetime(2026, 8, 21).date(), from_weekday=3) is True

    def test_thursday_too(self):
        assert should_wind_down(datetime(2026, 8, 20).date(), from_weekday=3) is True
