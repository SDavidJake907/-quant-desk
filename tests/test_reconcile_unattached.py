"""Positions closed by something other than their own bracket leg.

THE BUG, AS FOUND IN THE LIVE ACCOUNT
`classify` inspects only `order["legs"]` of the tracked entry. When a position
is closed by any OTHER order — a replacement bracket, a manual sale, a market
close — the entry's own legs show `expired` or `canceled`, nothing filled, and
the trade reads `FILLED_OPEN` forever.

Observed on 2026-08-18: four trades (IP, CEG, Q, SUPN) reported open while the
broker held no positions at all.

    smc-IP-1786125600  buy limit filled 41.74
          leg limit   expired
          leg stop    canceled          <- neither leg filled
    IP  sell limit  coid=1197eaad...    <- a different order entirely
          leg stop    filled 40.96      <- what actually closed it

WHY IT MATTERS MORE NOW THAN IT DID
`scripts/flatten.py` closes with DELETE /v2/positions/{symbol}, which creates
exactly such an unattached order. Every Friday-flattened trade would read as
open forever, so the frozen window's "100 completed trades" could never be
reached — and expectancy would be computed only from trades that closed via
their original bracket, which is the biased subset that hit stop or target
cleanly.
"""

from __future__ import annotations

from scanner.execution.reconcile import (
    CLOSED_FLAT,
    CLOSED_STOP,
    CLOSED_TARGET,
    FILLED_OPEN,
    reconcile,
)

INTENTS = {
    "smc-IP-1": {"symbol": "IP", "entry_price": 41.74, "stop_price": 41.04,
                 "target_price": 43.0, "shares": 10},
}


def _entry(coid="smc-IP-1", symbol="IP", filled="41.74", at="2026-08-10T14:00:00Z",
           legs=None):
    return {"client_order_id": coid, "symbol": symbol, "side": "buy",
            "type": "limit", "status": "filled", "filled_avg_price": filled,
            "qty": "10", "submitted_at": at, "filled_at": at,
            "legs": legs if legs is not None
            else [{"type": "limit", "status": "expired", "filled_avg_price": None},
                  {"type": "stop", "status": "canceled", "filled_avg_price": None}]}


def _unattached_sell(symbol="IP", type_="market", filled="40.96",
                     at="2026-08-12T18:00:00Z", legs=None):
    return {"client_order_id": "1197eaad-uuid", "symbol": symbol, "side": "sell",
            "type": type_, "status": "filled", "filled_avg_price": filled,
            "qty": "10", "submitted_at": at, "filled_at": at,
            "legs": legs or []}


def _for(symbol, trades):
    return [t for t in trades if t.symbol == symbol and t.tracked]


class TestUnattachedCloses:
    def test_a_market_close_closes_the_trade(self):
        """The SUPN case, and the one flatten.py will produce every Friday."""
        trades = reconcile(INTENTS, [_entry(), _unattached_sell()])
        ours = _for("IP", trades)[0]
        assert ours.state == CLOSED_FLAT
        assert ours.exit_fill == 40.96

    def test_a_replacement_bracket_stop_is_recognised_as_a_stop(self):
        """The IP case: a new sell order whose own stop leg filled."""
        closer = _unattached_sell(type_="limit", legs=[
            {"type": "stop", "status": "filled", "filled_avg_price": "40.96"}])
        ours = _for("IP", reconcile(INTENTS, [_entry(), closer]))[0]
        assert ours.state == CLOSED_STOP
        assert ours.exit_fill == 40.96

    def test_a_replacement_limit_is_recognised_as_a_target(self):
        closer = _unattached_sell(type_="limit", filled="43.10", legs=[])
        ours = _for("IP", reconcile(INTENTS, [_entry(), closer]))[0]
        assert ours.state == CLOSED_TARGET

    def test_a_trade_with_no_closing_order_stays_open(self):
        """A genuinely open position must not be closed by this logic."""
        ours = _for("IP", reconcile(INTENTS, [_entry()]))[0]
        assert ours.state == FILLED_OPEN

    def test_the_entrys_own_filled_leg_still_wins(self):
        """When the bracket did its job, nothing changes."""
        entry = _entry(legs=[
            {"type": "stop", "status": "filled", "filled_avg_price": "41.04"}])
        ours = _for("IP", reconcile(INTENTS, [entry, _unattached_sell()]))[0]
        assert ours.state == CLOSED_STOP
        assert ours.exit_fill == 41.04


class TestItDoesNotStealExits:
    def test_a_sell_before_the_entry_is_not_its_exit(self):
        """Chronology matters: an earlier sale closed something else."""
        early = _unattached_sell(at="2026-08-01T14:00:00Z")
        ours = _for("IP", reconcile(INTENTS, [_entry(), early]))[0]
        assert ours.state == FILLED_OPEN

    def test_a_sell_in_another_symbol_is_not_its_exit(self):
        other = _unattached_sell(symbol="CEG")
        ours = _for("IP", reconcile(INTENTS, [_entry(), other]))[0]
        assert ours.state == FILLED_OPEN

    def test_one_closing_order_closes_only_one_entry(self):
        """Two entries in the same name and one sale: the earlier entry takes
        it and the later one stays open, rather than both claiming it."""
        intents = dict(INTENTS)
        intents["smc-IP-2"] = dict(INTENTS["smc-IP-1"])
        orders = [
            _entry(coid="smc-IP-1", at="2026-08-10T14:00:00Z"),
            _entry(coid="smc-IP-2", at="2026-08-11T14:00:00Z"),
            _unattached_sell(at="2026-08-12T18:00:00Z"),
        ]
        states = sorted(t.state for t in _for("IP", reconcile(intents, orders)))
        assert states == [CLOSED_FLAT, FILLED_OPEN]

    def test_an_unfilled_sell_closes_nothing(self):
        pending = _unattached_sell()
        pending["status"] = "canceled"
        pending["filled_avg_price"] = None
        ours = _for("IP", reconcile(INTENTS, [_entry(), pending]))[0]
        assert ours.state == FILLED_OPEN


class TestParentCancelledLegFilled:
    """A replacement bracket whose PARENT is cancelled while a leg fills.

    That is what a stop doing its job looks like on Alpaca, and requiring a
    fill on the parent order missed all of them — IP sat open in the live
    account against a broker holding nothing.
    """

    def test_a_cancelled_parent_with_a_filled_stop_leg_closes_the_trade(self):
        closer = {
            "client_order_id": "1197eaad-uuid", "symbol": "IP", "side": "sell",
            "type": "limit", "status": "canceled", "filled_avg_price": None,
            "submitted_at": "2026-08-12T18:00:00Z",
            "filled_at": "2026-08-12T18:00:00Z",
            "legs": [{"type": "stop", "status": "filled",
                      "filled_avg_price": "40.96"}],
        }
        ours = _for("IP", reconcile(INTENTS, [_entry(), closer]))[0]
        assert ours.state == CLOSED_STOP
        assert ours.exit_fill == 40.96

    def test_a_cancelled_parent_with_no_filled_leg_closes_nothing(self):
        closer = {
            "client_order_id": "dead-uuid", "symbol": "IP", "side": "sell",
            "type": "limit", "status": "canceled", "filled_avg_price": None,
            "submitted_at": "2026-08-12T18:00:00Z",
            "legs": [{"type": "stop", "status": "canceled",
                      "filled_avg_price": None}],
        }
        ours = _for("IP", reconcile(INTENTS, [_entry(), closer]))[0]
        assert ours.state == FILLED_OPEN
