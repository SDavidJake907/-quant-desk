"""Extended-hours trading (WORKLIST §59): which session, and what an order looks like."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scanner.execution import extended, sessions
from scanner.execution.sessions import ET

# Mon 10/12 .. Fri 10/16 2026 are sessions; Sat/Sun are not.
DAYS = {date(2026, 10, d) for d in (9, 12, 13, 14, 15, 16)}


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.mark.parametrize("when, want", [
    (at(2026, 10, 12, 10, 0), "regular"),
    (at(2026, 10, 12, 4, 30), "pre"),
    (at(2026, 10, 12, 3, 59), "overnight"),     # Sunday-night overnight, before Monday's premarket
    (at(2026, 10, 12, 17, 0), "after"),
    (at(2026, 10, 12, 21, 0), "overnight"),     # Monday night into Tuesday
    (at(2026, 10, 11, 20, 30), "overnight"),    # SUNDAY 8:30 PM ET: the weekend window opens
    (at(2026, 10, 11, 12, 0), "closed"),        # Sunday midday
    (at(2026, 10, 10, 12, 0), "closed"),        # Saturday
    (at(2026, 10, 9, 20, 30), "closed"),        # Friday night: no overnight into Saturday
    (at(2026, 10, 9, 19, 0), "after"),          # Friday after-hours
])
def test_session_at(when, want):
    assert sessions.session_at(when, DAYS) == want


def test_extended_payload_is_a_plain_day_limit_with_extended_hours():
    p = extended.limit_payload("PLTR", 10, "buy", 205.123, "pck-PLTR-1")
    assert p == {"symbol": "PLTR", "qty": "10", "side": "buy", "type": "limit",
                 "limit_price": "205.12", "time_in_force": "day", "extended_hours": True,
                 "client_order_id": "pck-PLTR-1"}
    assert "order_class" not in p and "stop_loss" not in p


def test_zero_shares_is_refused():
    with pytest.raises(ValueError):
        extended.limit_payload("PLTR", 0, "buy", 1.0, "x")


def test_a_stale_price_is_never_used():
    now = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)
    assert extended.fresh_enough(now - timedelta(minutes=10), now, "after")
    assert not extended.fresh_enough(now - timedelta(minutes=20), now, "after")
    assert extended.fresh_enough(now - timedelta(minutes=40), now, "overnight")
    assert not extended.fresh_enough(now - timedelta(minutes=50), now, "overnight")


def test_an_after_hours_buy_is_a_plain_limit_and_logs_its_stop(monkeypatch, tmp_path):
    import asyncio
    from scanner.config import settings
    from scanner.execution import bookorders
    from scanner.execution.policy import TradingPolicy

    sent = []

    class FakeBroker:
        async def submit(self, payload):
            sent.append(payload)
            return {"id": "1"}

    async def fake_price_and_atr(symbol, client):
        return 100.0, 3.0

    async def fake_extended_price(symbol, client, session, now=None):
        return 101.0

    monkeypatch.setattr(bookorders, "price_and_atr", fake_price_and_atr)
    monkeypatch.setattr(bookorders.extended, "price", fake_extended_price)
    monkeypatch.setattr(settings, "audit_log_path", tmp_path / "audit.jsonl")
    res = asyncio.run(bookorders.buy_with_stop(
        FakeBroker(), None, "PLTR", strategy="picks", sleeve=40_000, room=40_000, cap_pct=12.5,
        policy=TradingPolicy(), reason="test", session="after"))
    assert res.ok and len(sent) == 1
    p = sent[0]
    assert p["extended_hours"] is True and p["time_in_force"] == "day" and p["type"] == "limit"
    assert "stop_loss" not in p and "order_class" not in p
    assert float(p["limit_price"]) > 101.0                  # priced off the live after-hours print
    logged = (tmp_path / "audit.jsonl").read_text()
    assert '"stop_price"' in logged                          # protect.py arms it at the open
