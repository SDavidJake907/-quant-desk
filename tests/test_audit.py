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
