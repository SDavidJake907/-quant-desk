"""Every sold position for Gemma (advisor/sold_review.py)."""

from datetime import datetime, timedelta, timezone

from scanner.advisor import sold_review as sr

T0 = datetime(2026, 10, 23, 21, 17, tzinfo=timezone.utc)


def fill(oid, side, qty, price, minutes, symbol="SNDK"):
    return {"id": f"{oid}-{minutes}", "order_id": oid, "symbol": symbol, "side": side, "qty": str(qty),
            "price": str(price), "transaction_time": (T0 + timedelta(minutes=minutes)).isoformat()}


ORDERS = {"b1": {"client_order_id": "gem-SNDK-1", "type": "limit"},
          "s1": {"client_order_id": "gem-SNDK-x1", "type": "limit"},
          "b2": {"client_order_id": "pck-AAA-1", "type": "limit"},
          "s2": {"type": "stop"}}


def test_partial_fills_are_one_trade():
    trips = sr.round_trips([fill("b1", "buy", 3, 1230, 0), fill("b1", "buy", 4, 1233.5, 0.5),
                            fill("s1", "sell", 7, 1237.4, 33)], ORDERS)
    assert len(trips) == 1
    t = trips[0]
    assert t.book == "gemma" and t.qty == 7 and round(t.entry, 2) == 1232.0
    assert round(t.dollars, 2) == round((1237.4 - 1232.0) * 7, 2)


def test_stop_fill_is_named_and_book_comes_from_the_buy():
    trips = sr.round_trips([fill("b2", "buy", 10, 50, 0, "AAA"), fill("s2", "sell", 10, 48, 90, "AAA")], ORDERS)
    assert trips[0].book == "picks" and trips[0].why == "stop hit"


def test_shares_bought_before_the_window_are_skipped_and_open_lots_stay_open():
    assert sr.round_trips([fill("s1", "sell", 7, 1237.4, 33)], ORDERS) == []
    assert sr.round_trips([fill("b1", "buy", 7, 1232, 0), fill("s1", "sell", 3, 1237.4, 33)], ORDERS) == []


def test_reason_from_the_audit_log_is_attached_and_trimmed():
    trips = sr.round_trips([fill("b1", "buy", 7, 1232, 0), fill("s1", "sell", 7, 1237.4, 33)], ORDERS)
    exits = [("SNDK", T0 + timedelta(minutes=32), "her sell-if met intraday: vs 50d -22.3%; after limit sell")]
    assert sr.with_reasons(trips, exits)[0].why == "her sell-if met intraday: vs 50d -22.3%"


def test_block_shows_the_move_since_the_sale_and_totals():
    trips = sr.round_trips([fill("b1", "buy", 7, 1232, 0), fill("s1", "sell", 7, 1237.4, 33)], ORDERS)
    text = sr.block(trips, {"SNDK": 1300.0})
    assert "SNDK (yours)" in text and "after 33 min" in text and "+5.1% since the sale" in text
    assert "1 rose 5%+ after the sale" in text
    assert sr.block([], {}) == ""
