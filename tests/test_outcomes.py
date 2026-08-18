"""Writing back what actually happened.

THE GAP
`logs/trading_audit.jsonl` records what was DECIDED and never what came of it.
Realized R is recomputed on demand from Alpaca's order history, which is not
permanent — the broker prunes it, and once it is gone the record of a trade's
outcome is gone with it.

Eight recorded-but-never-enforced dimensions are unanswerable until this
exists: the RSI gate, session slot, weekday, LLM ranking, the equal-lows pool,
liquidity tier, priority rank, and the uncapped-exit candidate. Each was logged
so it could be settled later on data nobody chose it from. "Later" needs the
outcome.

APPEND ONLY, ALWAYS
Earlier records are never rewritten. A `trade_outcome` event is appended and
linked by `client_order_id`, which is permanent and already on every intent.
The value of an append-only log is precisely that it cannot be revised after
the fact — an outcome written by editing the original intent would be
indistinguishable from an intent that always said that.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.execution.outcomes import (
    TradeOutcome,
    already_recorded,
    outcome_from,
)


class _Trade:
    """The shape `reconcile` produces."""

    def __init__(self, coid="smc-IP-1", symbol="IP", state="stop",
                 entry_fill=41.74, exit_fill=40.96, intended_entry=41.74,
                 intended_stop=41.04, intended_target=43.0, shares=10,
                 tracked=True):
        self.client_order_id = coid
        self.symbol = symbol
        self.state = state
        self.entry_fill = entry_fill
        self.exit_fill = exit_fill
        self.intended_entry = intended_entry
        self.intended_stop = intended_stop
        self.intended_target = intended_target
        self.shares = shares
        self.tracked = tracked

    @property
    def is_closed(self):
        return self.state in {"stop", "target", "closed", "flattened"}


class TestOutcomeExtraction:
    def test_a_closed_trade_yields_an_outcome(self):
        result = outcome_from(_Trade())
        assert isinstance(result, TradeOutcome)
        assert result.client_order_id == "smc-IP-1"
        assert result.exit_reason == "stop"

    def test_realized_r_is_measured_from_the_FILLS(self):
        """Not from intended prices. The whole point is what happened, and a
        trade that filled 0.35R better than its stop did not lose 1.00R."""
        # entry 41.74, stop 41.04 -> risk 0.70. Exit 40.96 is 0.78 below entry.
        result = outcome_from(_Trade())
        assert abs(result.realized_r - (-0.78 / 0.70)) < 1e-6

    def test_a_target_exit_is_positive(self):
        result = outcome_from(_Trade(state="target", exit_fill=43.10))
        assert result.realized_r > 0
        assert result.exit_reason == "target"

    def test_a_flattened_trade_is_recorded_with_its_own_reason(self):
        """Friday close-outs must be distinguishable from stops and targets, or
        the weekend rule's cost cannot be measured from the log."""
        result = outcome_from(_Trade(state="flattened", exit_fill=41.50))
        assert result.exit_reason == "flattened"

    def test_the_realised_dollars_use_the_share_count(self):
        result = outcome_from(_Trade())
        assert abs(result.realized_dollars - (40.96 - 41.74) * 10) < 1e-6


class TestRefusals:
    def test_an_open_trade_yields_nothing(self):
        assert outcome_from(_Trade(state="open", exit_fill=None)) is None

    def test_an_untracked_trade_yields_nothing(self):
        """A position opened by hand is not this strategy's evidence."""
        assert outcome_from(_Trade(tracked=False)) is None

    def test_a_trade_with_no_risk_yields_nothing(self):
        """No stop distance means no R. Reporting zero would be a lie that
        averages into the expectancy."""
        assert outcome_from(_Trade(intended_stop=41.74)) is None

    def test_a_missing_exit_fill_yields_nothing(self):
        assert outcome_from(_Trade(exit_fill=None)) is None


class TestIdempotence:
    def test_an_already_recorded_outcome_is_not_written_twice(self):
        """The job runs on a schedule and after every flatten. Writing an
        outcome twice would double-count the trade in every statistic drawn
        from the log."""
        seen = {"smc-IP-1"}
        assert already_recorded("smc-IP-1", seen) is True
        assert already_recorded("smc-CCL-9", seen) is False

    def test_an_empty_ledger_has_recorded_nothing(self):
        assert already_recorded("smc-IP-1", set()) is False
