

def test_the_24h_view_is_separate_from_the_trade_record():
    """Two different questions with two different evidence bars.

    `build_block` answers "what has this system learned", which needs a sample
    and refuses to speak below MIN_SAMPLE. `last_24h` answers "what just
    happened", which needs none -- a refusal is a fact about yesterday whether
    or not it is a fact about the strategy.

    Mixing them would be the worst of both: last night's handful of refusals
    reading like a track record.
    """
    import json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    import tempfile

    from scanner.llm import memory

    now = datetime.now(timezone.utc)
    rows = [
        {"at": (now - timedelta(hours=2)).isoformat(),
         "event": "candidate_refused", "symbol": "AAA",
         "reason": "reports earnings Thu"},
        {"at": (now - timedelta(hours=3)).isoformat(),
         "event": "order_submitted", "symbol": "BBB"},
        {"at": (now - timedelta(days=9)).isoformat(),
         "event": "candidate_refused", "symbol": "OLD",
         "reason": "ancient and must not appear"},
    ]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "audit.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows),
                        encoding="utf-8")
        recent = memory.last_24h(path, now=now)

    assert "reports earnings Thu" in recent
    assert "BBB" in recent
    assert "ancient" not in recent, "the window must exclude older events"
    assert "learned" not in recent.lower(), (
        "the 24h view must not claim to be a track record"
    )
