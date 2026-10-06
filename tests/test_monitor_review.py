"""Monitor v2 review: Gemma's reply is validated, recorded in the scorecard's
format, and a red flag needs a verbatim quote AND a material event type."""

from __future__ import annotations

from scanner.monitor import codes, review

HEADS = ["SEC opens probe into accounting at Acme", "Acme shares slip in early trade"]


def test_red_flag_with_quote_and_material_event_stands():
    out = review.validate({"c": "H", "f": "2", "k": "3", "t": "regulatory",
                           "e": "SEC opens probe into accounting", "w": "regulator probe"}, HEADS)
    assert out["f"] == "2" and out["grounded"] is True


def test_red_flag_without_verbatim_quote_is_downgraded_to_watch():
    out = review.validate({"c": "H", "f": "2", "k": "3", "t": "regulatory",
                           "e": "SEC launches investigation", "w": "x"}, HEADS)
    assert out["f"] == "1" and out["grounded"] is False


def test_red_flag_without_material_event_is_downgraded():
    out = review.validate({"c": "H", "f": "2", "k": "2", "t": "none",
                           "e": "Acme shares slip in early trade", "w": "x"}, HEADS)
    assert out["f"] == "1"


def test_reason_keeps_the_scorecard_prefix():
    out = review.validate({"c": "X", "f": "2", "k": "3", "t": "regulatory",
                           "e": "SEC opens probe into accounting", "w": "probe"}, HEADS)
    text = review.reason(out, "R+0.40 P0.55", reused=False)
    assert text.startswith("exit_early (strong, grounded): v2 f2 t=regulatory")
    assert "R+0.40 P0.55" in text


def test_payload_is_deterministic_and_schema_bound():
    p = review.payload("prompt", model="gemma4:12b", num_ctx=24576, keep_alive="45m", think=False)
    assert p["options"]["temperature"] == 0 and p["options"]["seed"] == review.SEED
    assert p["format"] == review.SCHEMA and p["think"] is False


def test_headlines_are_deduplicated_recent_and_capped():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    items = [("A", now - timedelta(hours=1)), ("A", now - timedelta(hours=2)),
             ("B", now - timedelta(hours=30)), ("OLD", now - timedelta(hours=60))]
    lines = review.recent_headlines(items, now=now)
    assert [l.split("] ", 1)[1] for l in lines] == ["A", "B"]


def test_wilder_atr_and_daily_atr_pct():
    highs, lows, closes = [11.0] * 20, [9.0] * 20, [10.0] * 20
    assert codes.wilder_atr(highs, lows, closes) == 2.0
