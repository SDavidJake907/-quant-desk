"""The ranked slate, and why the untaken candidates are the point.

`TradeIntent` is written only for a symbol that was bought. So every outcome
this system can observe was already filtered by rank, and the model's ranking
cannot be falsified from live trading however long it runs — its third-choice
calls never become orders and so never become evidence.

The `candidate_slate` record is what breaks that. These tests pin the two
properties that make it useful: it survives the round trip to JSONL intact,
and it keeps the candidates that were NOT traded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from scanner.execution.audit import append_event
from scanner.models import AuditEvent, SlateEntry


def _entry(symbol: str, llm_rank: int, priority_rank: int) -> SlateEntry:
    return SlateEntry(
        symbol=symbol,
        priority_rank=priority_rank,
        priority_score=26.79,
        llm_rank=llm_rank,
        llm_conviction="neutral",
        llm_driver="company",
        entry_price=262.83,
        stop_price=258.25,
        target_price=277.12,
        reward_risk=3.12,
        dollar_volume=53910349.36,
        liquidity_tier="over-50M",
        sector="Energy",
        rsi_gate_passed=True,
        trigger_at="2026-09-24T13:30:00-04:00",
        trigger_slot="midday",
        trigger_weekday="Thu",
    )


def _write(tmp_path, slate):
    log = tmp_path / "audit.jsonl"
    append_event(log, AuditEvent(
        event="candidate_slate", at=datetime.now(timezone.utc),
        slate=slate, stage="scan", policy="fb709122e949", build="deadbeef",
        reason=f"{len(slate)} candidate(s) ranked",
    ))
    return [json.loads(line) for line in
            log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_whole_slate_survives_the_round_trip(tmp_path):
    rows = _write(tmp_path, (
        _entry("ACN", 1, 1), _entry("PSX", 2, 3), _entry("IP", 3, 2),
    ))
    assert len(rows) == 1
    assert [e["symbol"] for e in rows[0]["slate"]] == ["ACN", "PSX", "IP"]


def test_candidates_that_were_never_bought_are_still_recorded(tmp_path):
    """THE WHOLE POINT. A slate holding only the taken symbol would leave the
    ranking exactly as unfalsifiable as it was before."""
    rows = _write(tmp_path, (_entry("ACN", 1, 1), _entry("PSX", 3, 4)))
    ranks = {e["symbol"]: e["llm_rank"] for e in rows[0]["slate"]}
    assert ranks["PSX"] == 3          # never ordered, still on the record


def test_the_model_rank_is_kept_apart_from_the_deterministic_one(tmp_path):
    """They disagree about half the time. Collapsing them would erase the only
    thing being evaluated."""
    rows = _write(tmp_path, (_entry("PSX", 2, 3),))
    entry = rows[0]["slate"][0]
    assert entry["llm_rank"] == 2
    assert entry["priority_rank"] == 3


def test_trigger_time_is_kept_so_replay_enters_where_the_scan_would(tmp_path):
    rows = _write(tmp_path, (_entry("ACN", 1, 1),))
    assert rows[0]["slate"][0]["trigger_at"] == "2026-09-24T13:30:00-04:00"


def test_levels_are_kept_so_an_untaken_candidate_can_be_resolved(tmp_path):
    """Replay needs entry, stop and target to score a trade that never
    happened. Without them an untaken candidate is just a name."""
    entry = _write(tmp_path, (_entry("IP", 3, 2),))[0]["slate"][0]
    assert entry["entry_price"] == 262.83
    assert entry["stop_price"] == 258.25
    assert entry["target_price"] == 277.12


def test_the_slate_carries_policy_and_build_like_every_other_record(tmp_path):
    row = _write(tmp_path, (_entry("ACN", 1, 1),))[0]
    assert row["policy"] == "fb709122e949"
    assert row["build"] == "deadbeef"


def test_a_slate_record_carries_no_intent(tmp_path):
    """Seen is not done. An intent on this record would make a ranked
    candidate indistinguishable from an order."""
    row = _write(tmp_path, (_entry("ACN", 1, 1),))[0]
    assert row["intent"] is None
    assert row["outcome"] is None


def test_ordinary_records_still_have_no_slate(tmp_path):
    log = tmp_path / "audit.jsonl"
    append_event(log, AuditEvent(
        event="candidate_refused", at=datetime.now(timezone.utc),
        symbol="LITE", reason="already holding",
    ))
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["slate"] is None
