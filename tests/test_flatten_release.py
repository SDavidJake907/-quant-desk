"""Closing a position only after the broker has released its shares.

THE FAILURE THIS PINS DOWN
2026-10-02 11:45 AK. The flatten cancelled INTC's bracket and sent the close
in the same instant. Alpaca had not yet released the shares the stop leg was
holding, and refused:

    insufficient qty available for order (requested: 169, available: 0)
    existing_qty 169, held_for_orders 169

The cancel is asynchronous. INTC went into the weekend open. Cancelling first
was right; not waiting for the cancel to land was the bug.
"""

from __future__ import annotations

from scanner.execution.alpaca import BrokerError
from scanner.execution.flatten import held_back
from scanner.execution.flatten_run import close_when_released


def _pos(symbol, qty, available):
    return {"symbol": symbol, "qty": str(qty), "qty_available": str(available)}


class TestHeldBack:
    def test_shares_reserved_by_a_live_leg_are_held_back(self):
        assert held_back([_pos("INTC", 169, 0)], ("INTC",)) == ("INTC",)

    def test_fully_released_shares_are_not_held_back(self):
        assert held_back([_pos("INTC", 169, 169)], ("INTC",)) == ()

    def test_a_short_is_compared_by_magnitude(self):
        assert held_back([_pos("TSLA", -5, -5)], ("TSLA",)) == ()

    def test_a_missing_qty_available_is_not_proof_of_release(self):
        assert held_back([{"symbol": "F", "qty": "2"}], ("F",)) == ("F",)

    def test_symbols_not_being_closed_are_ignored(self):
        assert held_back([_pos("AON", 73, 0)], ("INTC",)) == ()


class FakeBroker:
    """Shares stay reserved for `release_after` position reads, as a cancel
    that has been accepted but not yet processed."""

    def __init__(self, qty, release_after):
        self.qty, self.reads, self.release_after = qty, 0, release_after
        self.closed: list[str] = []
        self.close_attempts = 0

    def _available(self):
        return self.qty if self.reads > self.release_after else 0

    async def _get(self, path, **_):
        assert path == "/v2/positions"
        self.reads += 1
        if self.closed:
            return []
        return [_pos("INTC", self.qty, self._available())]

    async def close_position(self, symbol):
        self.close_attempts += 1
        if self._available() < self.qty:
            raise BrokerError("insufficient qty available for order "
                              "(requested: 169, available: 0)")
        self.closed.append(symbol)


async def _no_sleep(_):
    return None


class TestCloseWhenReleased:
    async def test_waits_for_the_cancel_to_land_then_closes(self):
        broker = FakeBroker(169, release_after=3)
        failed = await close_when_released(broker, ("INTC",), attempts=10,
                                           sleep=_no_sleep)
        assert failed == {}
        assert broker.closed == ["INTC"]

    async def test_never_sends_a_close_against_reserved_shares(self):
        broker = FakeBroker(169, release_after=3)
        await close_when_released(broker, ("INTC",), attempts=10, sleep=_no_sleep)
        assert broker.close_attempts == 1

    async def test_reports_a_symbol_that_never_releases(self):
        broker = FakeBroker(169, release_after=999)
        failed = await close_when_released(broker, ("INTC",), attempts=4,
                                           sleep=_no_sleep)
        assert set(failed) == {"INTC"}
        assert broker.closed == []

    async def test_a_rejected_close_is_retried_not_abandoned(self):
        """The broker can report shares free and still refuse a moment later;
        that race must cost a retry, not the weekend."""
        broker = FakeBroker(169, release_after=0)
        real_close = broker.close_position
        calls = {"n": 0}

        async def flaky(symbol):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BrokerError("insufficient qty available for order")
            await real_close(symbol)

        broker.close_position = flaky
        failed = await close_when_released(broker, ("INTC",), attempts=5,
                                           sleep=_no_sleep)
        assert failed == {}
        assert broker.closed == ["INTC"]

    async def test_a_failed_read_costs_a_retry_not_the_close_out(self):
        """A transient 5xx on the positions read must not abort the job before
        any close is sent -- that would be worse than the bug being fixed."""
        broker = FakeBroker(169, release_after=0)
        real_get = broker._get
        calls = {"n": 0}

        async def flaky_get(path, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BrokerError("503 service unavailable")
            return await real_get(path, **kw)

        broker._get = flaky_get
        failed = await close_when_released(broker, ("INTC",), attempts=5,
                                           sleep=_no_sleep)
        assert failed == {}
        assert broker.closed == ["INTC"]

    async def test_every_read_failing_reports_failure_without_raising(self):
        broker = FakeBroker(169, release_after=0)

        async def dead(path, **kw):
            raise BrokerError("timeout")

        broker._get = dead
        failed = await close_when_released(broker, ("INTC",), attempts=3,
                                           sleep=_no_sleep)
        assert set(failed) == {"INTC"}

    async def test_a_symbol_still_held_at_the_last_attempt_is_tried_anyway(self):
        """Waiting for release is a courtesy, not a veto. If the shares never
        show as free -- a cancel that never lands, a missing field -- the
        close must still be sent once rather than never."""
        broker = FakeBroker(169, release_after=999)
        await close_when_released(broker, ("INTC",), attempts=4, sleep=_no_sleep)
        assert broker.close_attempts == 1

    async def test_a_missing_qty_available_still_gets_closed(self):
        class NoField(FakeBroker):
            async def _get(self, path, **_):
                return [] if self.closed else [{"symbol": "INTC", "qty": "169"}]

            async def close_position(self, symbol):
                self.close_attempts += 1
                self.closed.append(symbol)

        broker = NoField(169, release_after=0)
        failed = await close_when_released(broker, ("INTC",), attempts=3,
                                           sleep=_no_sleep)
        assert failed == {} and broker.closed == ["INTC"]

    async def test_the_overall_deadline_stops_the_loop(self):
        broker = FakeBroker(169, release_after=999)
        clock = iter(range(0, 10_000, 100))
        failed = await close_when_released(
            broker, ("INTC",), attempts=50, sleep=_no_sleep,
            deadline_seconds=250, clock=lambda: next(clock))
        assert set(failed) == {"INTC"}
        assert broker.reads <= 4

    async def test_an_already_closed_position_counts_as_success(self):
        broker = FakeBroker(169, release_after=0)
        broker.closed.append("INTC")
        failed = await close_when_released(broker, ("INTC",), attempts=3,
                                           sleep=_no_sleep)
        assert failed == {}
