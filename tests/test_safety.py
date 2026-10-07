from scanner.execution.guards import RiskLimits
from scanner.execution.safety import inspect


def _account(**overrides):
    value = {"equity": "100000", "last_equity": "100000", "status": "ACTIVE"}
    value.update(overrides)
    return value


def _bracket(symbol="AAA"):
    return {"symbol": symbol, "order_class": "bracket", "status": "filled", "legs": [
        {"type": "limit", "status": "new"},
        {"type": "stop", "status": "held"},
    ]}


def test_healthy_filled_bracket_is_safe():
    report = inspect(_account(), [{"symbol": "AAA"}], [_bracket()],
                     {"a": {"symbol": "AAA"}}, RiskLimits())
    assert report.healthy


def test_open_position_without_both_legs_is_critical():
    order = _bracket()
    order["legs"] = [{"type": "stop", "status": "held"}]
    report = inspect(_account(), [{"symbol": "AAA"}], [order],
                     {"a": {"symbol": "AAA"}}, RiskLimits())
    assert any(f.code == "missing_protection" for f in report.critical)


def test_untracked_position_is_critical():
    report = inspect(_account(), [{"symbol": "AAA"}], [], {}, RiskLimits())
    assert any(f.code == "untracked_position" for f in report.critical)


def test_kill_switch_and_daily_loss_are_critical():
    report = inspect(_account(equity="96000", last_equity="100000"), [], [], {},
                     RiskLimits(), kill_switch=True)
    codes = {f.code for f in report.critical}
    assert {"kill_switch", "daily_loss"} <= codes


# --- ownership (WORKLIST §40): momentum holds have no stop and no intent by design

def _momentum_owner(symbols):
    return {s: "momentum" for s in symbols}


def test_momentum_positions_raise_no_false_alarms():
    momentum = [{"symbol": s} for s in ("MU", "WDC", "DELL")]
    report = inspect(_account(), momentum, [], {}, RiskLimits(max_positions=2),
                     owner_map=_momentum_owner(["MU", "WDC", "DELL"]))
    assert report.healthy, report.critical


def test_manual_position_still_counts_and_still_needs_a_stop():
    positions = [{"symbol": "MU"}, {"symbol": "NKE"}]
    report = inspect(_account(), positions, [], {"m": {"symbol": "NKE"}},
                     RiskLimits(max_positions=8),
                     owner_map={"MU": "momentum", "NKE": "manual"})
    assert {(f.code, f.symbol) for f in report.critical} == {("missing_protection", "NKE")}


def test_slots_count_only_non_momentum_positions():
    positions = [{"symbol": s} for s in ("A", "B", "C")]
    intents = {s: {"symbol": s} for s in ("A", "B", "C")}
    orders = [_bracket(s) for s in ("A", "B", "C")]
    owners = {"A": "momentum", "B": "manual", "C": "manual"}
    assert inspect(_account(), positions, orders, intents, RiskLimits(max_positions=2),
                   owner_map=owners).healthy
    assert not inspect(_account(), positions, orders, intents,
                       RiskLimits(max_positions=2)).healthy        # no map: old behaviour


def test_oco_from_protect_counts_as_protection():
    oco = {"symbol": "AAA", "order_class": "oco", "type": "limit", "status": "new",
           "legs": [{"type": "stop", "status": "held"}]}
    report = inspect(_account(), [{"symbol": "AAA"}], [oco],
                     {"a": {"symbol": "AAA"}}, RiskLimits())
    assert report.healthy, report.critical
