import json
from datetime import date, datetime, timedelta

from scanner.ops import health as h

TODAY = date(2026, 10, 21)
NOW = datetime.fromisoformat("2026-10-21T06:00:00-08:00")


def _levels(findings):
    return [f.level for f in findings]


def test_every_share_needs_a_live_stop_held_legs_count():
    positions = [{"symbol": "MU", "qty": "8"}, {"symbol": "WDC", "qty": "7"},
                 {"symbol": "DDOG", "qty": "31"}]
    orders = [
        {"symbol": "MU", "side": "buy", "type": "limit", "status": "filled", "qty": "8", "legs": [
            {"symbol": "MU", "side": "sell", "type": "stop", "status": "held", "qty": "8"},
            {"symbol": "MU", "side": "sell", "type": "limit", "status": "held", "qty": "8"}]},
        {"symbol": "WDC", "side": "sell", "type": "stop", "status": "canceled", "qty": "7"},
        {"symbol": "DDOG", "side": "sell", "type": "stop", "status": "new", "qty": "20"},
    ]
    out = h.stop_gaps(positions, orders)
    texts = " ".join(f.text for f in out)
    assert _levels(out) == ["problem", "problem"]
    assert "WDC" in texts and "no live stop" in texts
    assert "DDOG" in texts and "20 of 31" in texts
    assert "MU" not in texts


def test_stop_gaps_ok_when_covered():
    out = h.stop_gaps([{"symbol": "X", "qty": "5"}],
                      [{"symbol": "X", "side": "sell", "type": "trailing_stop", "status": "accepted",
                        "qty": "5"}])
    assert _levels(out) == ["ok"]


def test_task_results():
    tasks = [
        {"name": "stock-scanner-gemma", "state": "Ready", "result": 0, "last": "2026-10-21T05:45:00"},
        {"name": "stock-scanner-weekly", "state": "Ready", "result": 1, "last": "2026-10-16T07:00:00"},
        {"name": "stock-scanner-advisor-pre", "state": "Disabled", "result": 1, "last": ""},
        {"name": "stock-scanner-scan", "state": "Running", "result": 267009, "last": ""},
        {"name": "stock-scanner-new", "state": "Ready", "result": 267011, "last": ""},
    ]
    out = h.task_problems(tasks)
    assert _levels(out) == ["problem"]
    assert "weekly" in out[0].text and "0x1" in out[0].text


def test_orphan_runner_is_flagged_loaded_runner_is_not():
    procs = [
        {"pid": 10, "ppid": 1, "name": "ollama.exe", "cmd": "ollama.exe serve"},
        {"pid": 11, "ppid": 10, "name": "ollama.exe", "cmd": "ollama.exe runner --model x"},
        {"pid": 12, "ppid": 999, "name": "llama-server.exe", "cmd": "llama-server.exe -m y"},
    ]
    out = h.orphan_runners(procs)
    assert _levels(out) == ["watch", "problem"]
    assert "12" in out[1].text


def test_orphan_check_quiet_with_nothing_loaded():
    assert _levels(h.orphan_runners([{"pid": 10, "ppid": 1, "name": "ollama.exe",
                                      "cmd": "ollama.exe serve"}])) == ["ok"]


def test_lessons_must_be_from_this_morning():
    fresh = {"at": (NOW - timedelta(minutes=50)).isoformat(), "text": "x" * 100}
    assert h.lessons_finding(fresh, now=NOW).level == "ok"
    old = {"at": (NOW - timedelta(hours=10)).isoformat(), "text": "x"}
    assert h.lessons_finding(old, now=NOW).level == "problem"
    afternoon = NOW + timedelta(hours=8, minutes=15)          # the 14:15 check, lessons from 05:10
    assert h.lessons_finding(fresh, now=afternoon).level == "ok"
    assert h.lessons_finding(None, now=NOW).level == "problem"
    assert h.lessons_finding({"at": "junk"}, now=NOW).level == "problem"


def _decision(at, submitted):
    return {"at": at, "event": "gemma_decision", "reason": json.dumps({"submitted": submitted,
                                                                       "buys": ["DDOG"], "sells": []})}


def test_decision_must_be_submitted_today():
    rows = [_decision("2026-10-20T05:46:00-08:00", True), _decision("2026-10-21T06:20:00-08:00", False)]
    assert h.decision_finding(rows, TODAY).level == "problem"
    rows.append(_decision("2026-10-21T05:46:00-08:00", True))
    f = h.decision_finding(rows, TODAY)
    assert f.level == "ok" and "DDOG" in f.text


def test_review_saved(tmp_path):
    assert h.review_finding(tmp_path, date(2026, 10, 20)).level == "problem"
    (tmp_path / "2026-10-20.json").write_text("{}", encoding="utf-8")
    assert h.review_finding(tmp_path, date(2026, 10, 20)).level == "ok"


def test_render_headline():
    ok = [h.Finding(level="ok", text="fine")]
    assert h.render(ok, now=NOW).splitlines()[0].endswith("ALL OK")
    bad = ok + [h.Finding(level="problem", text="MU has no stop")]
    text = h.render(bad, now=NOW)
    assert "1 PROBLEM" in text.splitlines()[0] and "MU has no stop" in text
    assert h.summary(bad) == "1 problem: MU has no stop"
