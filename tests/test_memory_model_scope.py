"""The review memory shows only the current model's own calls.

On 2026-10-04 the model went from gemma3:12b to gemma4:12b. Monday's first
review would have been shown six gemma3 "hold" calls on INTC as what the
system had just done -- one model's words in another's prompt. Rows written
before the `model` field existed belong to the earlier model.
"""

import json
from datetime import datetime

from scanner.llm.memory import last_24h

NOW = datetime.fromisoformat("2026-10-05T06:00:00-08:00")


def _write(tmp_path, rows):
    path = tmp_path / "audit.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def _review(symbol, reason, at, model=None):
    row = {"event": "position_review", "symbol": symbol, "at": at, "reason": reason}
    if model is not None:
        row["model"] = model
    return row


def test_only_the_current_models_reviews_are_shown(tmp_path):
    path = _write(tmp_path, [
        _review("INTC", "hold (neutral, grounded): old model", "2026-10-04T12:03:00-08:00"),
        _review("TSCO", "exit (strong, grounded): new model", "2026-10-05T05:40:00-08:00",
                model="gemma4:12b"),
    ])
    block = last_24h(path, now=NOW, model="gemma4:12b")
    assert "new model" in block
    assert "old model" not in block
    assert "1 review(s) by an earlier model left out" in block


def test_without_a_model_every_review_is_shown_as_before(tmp_path):
    path = _write(tmp_path, [
        _review("INTC", "hold (neutral, grounded): old model", "2026-10-04T12:03:00-08:00"),
    ])
    assert "old model" in last_24h(path, now=NOW)


def test_a_flatten_is_reported_as_what_the_system_did(tmp_path):
    # The 10-04 INTC close was sent by flatten.py, not the scan, and the block
    # said "0 order(s) sent" -- a review would not know the close was queued.
    path = _write(tmp_path, [{"event": "weekend_flatten", "at": "2026-10-04T19:24:27-08:00",
                              "reason": "cancelled 0, closed 1 of 1, flat=False, STILL OPEN: INTCx169"}])
    block = last_24h(path, now=NOW)
    assert "flatten" in block and "INTCx169" in block
