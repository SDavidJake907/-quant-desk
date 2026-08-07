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
