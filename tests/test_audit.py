from datetime import datetime, timezone
import json

from scanner.execution.audit import append_event
from scanner.models import AuditEvent, TradeIntent


def test_audit_is_append_only_jsonl(tmp_path):
    path = tmp_path / "logs" / "audit.jsonl"
    intent = TradeIntent(
        symbol="MU", action="buy",
        trigger_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        entry_price=100, stop_price=95, target_price=110, shares=2,
        risk_dollars=10, position_dollars=200, reward_risk=2,
    )
    append_event(path, AuditEvent(
        event="trade_intent", at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        symbol="MU", intent=intent,
    ))
    append_event(path, AuditEvent(
        event="order_rejected", at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        symbol="MU", reason="paper broker unavailable",
    ))

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["trade_intent", "order_rejected"]
    assert rows[0]["intent"]["paper"] is True
    assert rows[1]["reason"] == "paper broker unavailable"


def test_audit_can_record_broker_request_id(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_event(path, AuditEvent(
        event="order_rejected", at=datetime.now(timezone.utc),
        request_id="req-123", reason="rejected",
    ))
    assert json.loads(path.read_text())["request_id"] == "req-123"


# ---- reading the levels back out --------------------------------------
#
# Once a `day` bracket's legs are cancelled at the close, the log is the ONLY
# surviving record of what stop the position was sized against. These pin the
# reader that recovers them.

def _submitted(path, symbol, stop, target, at_hour=10):
    from scanner.models import TradeIntent
    append_event(path, AuditEvent(
        event="order_submitted",
        at=datetime(2026, 8, 7, at_hour, tzinfo=timezone.utc), symbol=symbol,
        intent=TradeIntent(
            symbol=symbol, action="buy", trigger_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            entry_price=100.0, stop_price=stop, target_price=target, shares=10,
            risk_dollars=50.0, position_dollars=1000.0, reward_risk=2.0,
        ),
    ))


def test_levels_come_back_per_symbol(tmp_path):
    from scanner.execution.audit import submitted_levels

    path = tmp_path / "audit.jsonl"
    _submitted(path, "CEG", 265.0, 280.0)
    _submitted(path, "Q", 133.59, 146.08)
    assert submitted_levels(path) == {"CEG": (265.0, 280.0), "Q": (133.59, 146.08)}


def test_the_latest_submission_wins(tmp_path):
    """A name re-entered after a stop-out is a different trade at different
    levels. Re-arming with the FIRST stop would protect the new position at
    the old trade's price."""
    from scanner.execution.audit import submitted_levels

    path = tmp_path / "audit.jsonl"
    _submitted(path, "Q", 133.59, 146.08, at_hour=10)
    _submitted(path, "Q", 141.00, 152.00, at_hour=15)
    assert submitted_levels(path)["Q"] == (141.00, 152.00)


def test_only_submitted_orders_count(tmp_path):
    """A `trade_intent` is something the scanner considered. An order that
    was rejected or never sent has no position behind it, so its levels must
    not be used to arm anything."""
    from scanner.models import TradeIntent
    from scanner.execution.audit import submitted_levels

    path = tmp_path / "audit.jsonl"
    append_event(path, AuditEvent(
        event="trade_intent", at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        symbol="NVDA", intent=TradeIntent(
            symbol="NVDA", action="buy", trigger_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            entry_price=100.0, stop_price=90.0, target_price=120.0, shares=10,
            risk_dollars=50.0, position_dollars=1000.0, reward_risk=2.0,
        ),
    ))
    assert "NVDA" not in submitted_levels(path)


def test_a_missing_log_is_empty_not_an_error(tmp_path):
    from scanner.execution.audit import submitted_levels

    assert submitted_levels(tmp_path / "nope.jsonl") == {}


def test_a_torn_final_line_does_not_lose_the_whole_log(tmp_path):
    """A power cut mid-write leaves a half-written line — the exact way this
    log ends when the machine dies. One bad line must not cost every level."""
    from scanner.execution.audit import submitted_levels

    path = tmp_path / "audit.jsonl"
    _submitted(path, "CEG", 265.0, 280.0)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "order_submitted", "symbol": "IP", "int')
    assert submitted_levels(path) == {"CEG": (265.0, 280.0)}
