import json
from datetime import datetime, timedelta

from scanner.advisor import hindsight_memory as hm

NOW = datetime.fromisoformat("2026-10-20T05:30:00-08:00")


def _decision(at, submitted=True):
    return {"at": at, "event": "gemma_decision", "reason": json.dumps({
        "keep": ["MPC"], "buys": ["MPC"], "sells": [], "reasons": {"MPC": "refining margins"},
        "journal": "core energy", "submitted": submitted, "session": "regular"})}


def test_memories_keep_her_submitted_decisions_and_her_own_trades_only():
    rows = [
        _decision("2026-10-06T05:45:00-08:00"),                    # before the challenge
        _decision("2026-10-08T05:45:00-08:00"),
        _decision("2026-10-08T06:00:00-08:00", submitted=False),  # a preview
        {"at": "2026-10-14T13:30:00-08:00", "event": "trade_outcome", "outcome": {
            "client_order_id": "gem-COHR-1", "symbol": "COHR", "exit_reason": "stop",
            "shares": 8, "entry_fill": 317.71, "exit_fill": 272.85,
            "realized_dollars": -359.0, "realized_r": -0.97}},
        {"at": "2026-10-14T13:30:00-08:00", "event": "trade_outcome", "outcome": {
            "client_order_id": "pck-MU-1", "symbol": "MU", "entry_fill": 1, "exit_fill": 2,
            "realized_dollars": 1, "realized_r": 1}},             # the operator's book
        {"at": "2026-10-19T10:25:00-08:00", "event": "gemma_exit", "symbol": "LITE",
         "reason": "operator ordered"},
    ]
    mem = hm.memories(rows, [{"day": "2026-10-19", "at": "2026-10-19T12:30:00-08:00",
                              "summary": "flat", "positions": [{"symbol": "MPC", "verdict": "keep",
                                                                "reason": "intact"}]}])
    kinds = [m["kind"] for m in mem]
    assert kinds == ["decision", "outcome", "exit", "review"]
    assert "-359 dollars" in mem[1]["text"] and "-0.97R" in mem[1]["text"]
    assert len({m["id"] for m in mem}) == len(mem)              # stable, unique ids
    assert "MU" not in mem[1]["text"]


def test_block_is_labelled_and_fails_open():
    fresh = {"at": (NOW - timedelta(hours=1)).isoformat(), "text": "Cut losers fast."}
    out = hm.block(fresh, now=NOW)
    assert out.startswith(hm.HEADER) and "Cut losers fast." in out
    assert "today's facts below win" in out
    assert hm.block(None, now=NOW) == ""
    assert hm.block({"at": "garbage", "text": "x"}, now=NOW) == ""
    assert hm.block({"at": fresh["at"], "text": "  "}, now=NOW) == ""
    stale = {"at": (NOW - hm.MAX_AGE - timedelta(minutes=1)).isoformat(), "text": "old"}
    assert hm.block(stale, now=NOW) == ""


def test_block_is_capped():
    long = {"at": NOW.isoformat(), "text": ("line of lesson text\n" * 400)}
    out = hm.block(long, now=NOW)
    assert len(out) <= len(hm.HEADER) + hm.MAX_CHARS + 10


def test_read_lessons_missing_file(tmp_path):
    assert hm.read_lessons(tmp_path / "nope.json") is None
