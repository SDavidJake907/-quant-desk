from scanner.execution import sell_queue as sq

Q = {"MU": "operator: sell the red ones", "NUE": "operator: sell the red ones"}


def test_due_when_held_and_no_sell_working():
    assert sq.due(Q, held={"MU", "NUE"}, open_orders=[]) == ["MU", "NUE"]


def test_not_due_while_a_sell_limit_is_working():
    working = [{"symbol": "MU", "side": "sell", "type": "limit", "status": "new"}]
    assert sq.due(Q, held={"MU", "NUE"}, open_orders=working) == ["NUE"]


def test_a_working_stop_does_not_count_as_a_sale():
    stop = [{"symbol": "NUE", "side": "sell", "type": "stop", "status": "held"}]
    assert sq.due(Q, held={"NUE"}, open_orders=stop) == ["NUE"]


def test_an_oco_or_bracket_target_does_not_count_as_a_sale():
    """protect.py's OCO is a 'limit' sell at the top level -- it is protection, not a sale."""
    oco = [{"symbol": "NUE", "side": "sell", "type": "limit", "status": "new", "order_class": "oco",
            "legs": [{"symbol": "NUE", "side": "sell", "type": "stop", "status": "held"}]},
           {"symbol": "MU", "side": "sell", "type": "limit", "status": "held", "order_class": "bracket"}]
    assert sq.due(Q, held={"MU", "NUE"}, open_orders=oco) == ["MU", "NUE"]


def test_prune_drops_what_is_no_longer_held():
    assert sq.prune(Q, held={"NUE"}) == {"NUE": "operator: sell the red ones"}


def test_add_keeps_the_first_reason_and_does_not_mutate():
    q = {"NUE": "operator"}
    out = sq.add(q, {"NUE": "monitor", "VLO": "her sell-if met"})
    assert out == {"NUE": "operator", "VLO": "her sell-if met"}
    assert q == {"NUE": "operator"}
