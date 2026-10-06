"""Post-entry monitoring: phone alerts and account-level hard stops.

The operator chose (2026-10-06) ntfy.sh for alerts and GO_LIVE's limits:
3% in a day, 6% in a week, 15% below the equity high. Built record-only:
a breach is logged and pushed, and the kill switch is only created with
--enforce, which stays off until funding.
"""

from __future__ import annotations

from datetime import date

from scanner.ops import notify, risk, watch


# --- ntfy -----------------------------------------------------------------

def test_ntfy_request_puts_title_priority_and_tags_in_headers():
    url, headers, body = notify.request("https://ntfy.sh", "my-topic", "Stop hit",
                                        "INTC sold 169 at 115.85", priority="high", tags=("warning",))
    assert url == "https://ntfy.sh/my-topic"
    assert headers["Title"] == "Stop hit" and headers["Priority"] == "high"
    assert headers["Tags"] == "warning"
    assert body == "INTC sold 169 at 115.85".encode()


def test_ntfy_titles_are_kept_ascii_safe():
    _, headers, _ = notify.request("https://ntfy.sh", "t", "Fill — INTC", "x")
    headers["Title"].encode("latin-1")          # must not raise


# --- risk limits -----------------------------------------------------------

LIMITS = risk.Limits(day_pct=3.0, week_pct=6.0, drawdown_pct=15.0)


def test_inside_every_limit_is_quiet():
    assert risk.evaluate(equity=99_000, day_start=100_000, week_start=100_000,
                         peak=102_000, limits=LIMITS) == []


def test_each_limit_trips_on_its_own():
    day = risk.evaluate(equity=96_900, day_start=100_000, week_start=96_000, peak=96_900, limits=LIMITS)
    week = risk.evaluate(equity=93_900, day_start=94_000, week_start=100_000, peak=94_000, limits=LIMITS)
    dd = risk.evaluate(equity=84_900, day_start=85_000, week_start=85_000, peak=100_000, limits=LIMITS)
    assert [b.name for b in day] == ["day"]
    assert [b.name for b in week] == ["week"]
    assert [b.name for b in dd] == ["drawdown"]
    assert dd[0].loss_pct == 15.1


def test_week_start_is_the_last_close_before_this_monday():
    history = [(date(2026, 10, 1), 101_000.0), (date(2026, 10, 2), 102_000.0),
               (date(2026, 10, 5), 102_041.0)]
    assert risk.week_start(history, today=date(2026, 10, 6)) == 102_000.0
    # no close before this week: fall back to the first value
    assert risk.week_start(history[2:], today=date(2026, 10, 6)) == 102_041.0


# --- what reaches the phone --------------------------------------------------

def test_watch_turns_audit_events_into_alerts_and_skips_noise():
    rows = [
        {"event": "position_review", "symbol": "INTC", "reason": "hold"},
        {"event": "protection_closed", "symbol": "INTC", "reason": "close_stop: market sell 169 sh"},
        {"event": "weekend_flatten", "symbol": "", "reason": "closed 1 of 1, flat=False, STILL OPEN: INTCx169"},
        {"event": "weekend_flatten", "symbol": "", "reason": "closed 2 of 2, flat=True"},
        {"event": "order_submitted", "symbol": "SLB", "reason": ""},
    ]
    alerts = watch.from_audit(rows)
    titles = [a.title for a in alerts]
    assert "Position review" not in " ".join(titles)
    assert any(a.priority == "urgent" and "INTC" in a.message for a in alerts)   # not flat
    assert any("Stop" in a.title and a.priority == "high" for a in alerts)
    assert sum(1 for a in alerts if "flatten" in a.title.lower()) == 1         # flat=True is quiet
    assert any("SLB" in a.message for a in alerts)


def test_watch_formats_fills():
    acts = [{"activity_type": "FILL", "symbol": "SLB", "side": "buy", "qty": "409",
             "price": "49.80", "id": "a1"}]
    (alert,) = watch.from_fills(acts)
    assert "SLB" in alert.title and "409" in alert.message and "49.80" in alert.message
