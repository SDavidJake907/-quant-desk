"""Going flat before the weekend.

WHY THIS IS THE MOST DANGEROUS CODE IN THE REPO
Everything else here either reads, or opens a position behind a stop. This
CLOSES positions at market, and it runs unattended on a schedule. A bug that
opens a bad trade costs one stop; a bug here can flatten a book at the wrong
moment, or fail silently and leave a position exposed over exactly the weekend
it was written to avoid.

So the order of operations is pinned by test:

  1. cancel the protective legs FIRST. Alpaca reserves the shares behind a
     resting stop or limit, so a close submitted while a bracket is live can
     be rejected for insufficient quantity -- and a rejected close reads like
     nothing happened.
  2. close the position.
  3. report what could not be closed, loudly, rather than exiting 0.

It must also be safe to run twice. A retry, a manual run, or a task that fires
after a partial failure must not throw or double-submit.
"""

from __future__ import annotations

from datetime import date

import pytest

from scanner.execution.flatten import FlattenPlan, plan_flatten, should_wind_down


class TestWindDown:
    """Thursday onward, stop opening positions that would run into the close."""

    def test_monday_through_wednesday_trade_normally(self):
        for day in (17, 18, 19):            # Mon, Tue, Wed of 2026-08
            assert should_wind_down(date(2026, 8, day), from_weekday=3) is False

    def test_thursday_starts_the_wind_down(self):
        assert should_wind_down(date(2026, 8, 20), from_weekday=3) is True

    def test_friday_is_still_winding_down(self):
        assert should_wind_down(date(2026, 8, 21), from_weekday=3) is True

    def test_the_start_day_is_configurable(self):
        """Friday-only is a different, weaker rule; it must be expressible."""
        assert should_wind_down(date(2026, 8, 20), from_weekday=4) is False
        assert should_wind_down(date(2026, 8, 21), from_weekday=4) is True


class TestPlan:
    def test_protective_legs_are_cancelled_before_the_close(self):
        plan = plan_flatten(
            positions=[{"symbol": "AAPL", "qty": "10"}],
            orders=[{"id": "leg-1", "symbol": "AAPL", "status": "new"}],
        )
        assert plan.cancel_first == ("leg-1",)
        assert plan.close == ("AAPL",)

    def test_a_position_with_no_working_orders_is_still_closed(self):
        """An unprotected position is the one most needing to be flat."""
        plan = plan_flatten(positions=[{"symbol": "MU", "qty": "5"}], orders=[])
        assert plan.cancel_first == ()
        assert plan.close == ("MU",)

    def test_orders_on_symbols_with_no_position_are_still_cancelled(self):
        """A resting entry that has not filled must not be left to fill on
        Monday morning into a book that is supposed to be flat."""
        plan = plan_flatten(
            positions=[],
            orders=[{"id": "entry-9", "symbol": "NVDA", "status": "new"}],
        )
        assert plan.cancel_first == ("entry-9",)
        assert plan.close == ()

    def test_already_filled_or_cancelled_orders_are_left_alone(self):
        plan = plan_flatten(
            positions=[{"symbol": "AAPL", "qty": "1"}],
            orders=[{"id": "done", "symbol": "AAPL", "status": "filled"},
                    {"id": "gone", "symbol": "AAPL", "status": "canceled"},
                    {"id": "live", "symbol": "AAPL", "status": "accepted"}],
        )
        assert plan.cancel_first == ("live",)

    def test_an_empty_book_produces_an_empty_plan(self):
        """The common case on a quiet week. It must be a no-op, not an error."""
        plan = plan_flatten(positions=[], orders=[])
        assert plan.is_empty is True
        assert plan.cancel_first == () and plan.close == ()

    def test_a_zero_quantity_position_is_not_closed(self):
        """Alpaca can report a position mid-settlement; closing zero shares is
        a rejected order and a confusing log line."""
        plan = plan_flatten(positions=[{"symbol": "F", "qty": "0"}], orders=[])
        assert plan.close == ()

    def test_every_symbol_appears_once_however_many_legs_it_has(self):
        plan = plan_flatten(
            positions=[{"symbol": "AAPL", "qty": "10"}],
            orders=[{"id": "a", "symbol": "AAPL", "status": "new"},
                    {"id": "b", "symbol": "AAPL", "status": "new"}],
        )
        assert plan.close == ("AAPL",)
        assert sorted(plan.cancel_first) == ["a", "b"]


class TestPlanIsInert:
    def test_building_a_plan_sends_nothing(self):
        """`plan_flatten` is pure so it can be dry-run and printed before any
        order leaves the machine. Nothing here may reach a broker."""
        plan = plan_flatten(positions=[{"symbol": "AAPL", "qty": "3"}],
                            orders=[{"id": "x", "symbol": "AAPL", "status": "new"}])
        assert isinstance(plan, FlattenPlan)
        assert plan.describe()
        assert "AAPL" in plan.describe()
