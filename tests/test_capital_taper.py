"""Deploying less capital as the weekend approaches.

NOT AN EDGE CLAIM, AND THE DISTINCTION MATTERS
Measured on 125 setups, expectancy by weekday is Mon +0.539R, Tue +0.054R,
Wed -0.562R, Thu +0.780R, Fri +1.068R -- and a permutation test says a random
five-way split produces a best day at least that good 18.9% of the time. Train
and holdout disagree about which day wins. There is no weekday edge here.

The taper is a risk schedule: less capital committed the closer the market gets
to a window where nothing can be managed. It reduces exposure, and exposure and
return scale together on this strategy (return/drawdown sits between 4.7 and
5.1 across every deployment level tested). So this costs return roughly in
proportion, and buys a smaller book going into the weekend.

Sizing already derives shares from the stop, so the taper works by scaling the
POSITION CAP for the day. Nothing else in the sizing path changes.
"""

from __future__ import annotations

from datetime import date

import pytest

from scanner.execution.policy import TradingPolicy
from scanner.execution.taper import deployment_for, position_cap_for


class TestSchedule:
    def test_monday_deploys_the_most(self):
        p = TradingPolicy()
        mon = deployment_for(date(2026, 8, 17), p)      # Monday
        tue = deployment_for(date(2026, 8, 18), p)
        wed = deployment_for(date(2026, 8, 19), p)
        assert mon > tue > wed

    def test_thursday_and_friday_deploy_nothing_new(self):
        """The wind-down already refuses new positions from Thursday; the
        taper must agree with it rather than quietly contradict it."""
        p = TradingPolicy()
        assert deployment_for(date(2026, 8, 20), p) == 0.0
        assert deployment_for(date(2026, 8, 21), p) == 0.0

    def test_the_schedule_is_a_policy_field(self):
        p = TradingPolicy(weekday_deployment_pct=(80.0, 60.0, 40.0, 0.0, 0.0))
        assert deployment_for(date(2026, 8, 17), p) == 80.0
        assert deployment_for(date(2026, 8, 19), p) == 40.0

    def test_a_weekend_date_deploys_nothing(self):
        p = TradingPolicy()
        assert deployment_for(date(2026, 8, 22), p) == 0.0   # Saturday
        assert deployment_for(date(2026, 8, 23), p) == 0.0   # Sunday


class TestPositionCap:
    def test_the_cap_scales_so_the_book_reaches_the_target(self):
        """Five slots at a 10% cap deploys 50%. The cap is what the sizer
        reads, so that is where the schedule has to land."""
        p = TradingPolicy(max_positions=5, max_position_pct=20.0,
                          weekday_deployment_pct=(50.0, 40.0, 30.0, 0.0, 0.0))
        assert position_cap_for(date(2026, 8, 17), p) == pytest.approx(10.0)
        assert position_cap_for(date(2026, 8, 18), p) == pytest.approx(8.0)

    def test_the_cap_never_exceeds_the_policy_maximum(self):
        """A schedule asking for more than the book allows must not widen the
        per-position cap past what was validated."""
        p = TradingPolicy(max_positions=5, max_position_pct=20.0,
                          weekday_deployment_pct=(500.0, 0.0, 0.0, 0.0, 0.0))
        assert position_cap_for(date(2026, 8, 17), p) == pytest.approx(20.0)

    def test_a_zero_day_gives_a_zero_cap(self):
        p = TradingPolicy()
        assert position_cap_for(date(2026, 8, 20), p) == 0.0


class TestPolicy:
    def test_the_default_schedule_tapers_toward_the_weekend(self):
        schedule = TradingPolicy().weekday_deployment_pct
        assert len(schedule) == 5
        assert schedule[0] >= schedule[1] >= schedule[2]
        assert schedule[3] == 0.0 and schedule[4] == 0.0

    def test_the_schedule_changes_the_fingerprint(self):
        base = TradingPolicy()
        other = TradingPolicy(weekday_deployment_pct=(100.0, 100.0, 100.0, 0.0, 0.0))
        assert base.fingerprint != other.fingerprint
