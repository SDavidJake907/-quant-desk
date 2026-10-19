"""Daily reconcile: dry-run previews are not lost orders."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "daily_reconcile.py"
_spec = importlib.util.spec_from_file_location("scripts_daily_reconcile", _PATH)
daily_reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily_reconcile)


def _write(path: Path, rows: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def test_dry_run_intents_are_skipped(tmp_path):
    log = _write(tmp_path / "audit.jsonl", [
        {"event": "trade_intent",
         "intent": {"client_order_id": "smc-PREVIEW-1", "dry_run": True}},
        {"event": "trade_intent",
         "intent": {"client_order_id": "pck-REAL-1", "dry_run": False}},
        {"event": "order_submitted",
         "intent": {"client_order_id": "gem-REAL-2"}},
    ])
    intents = daily_reconcile._load_intents(log)
    assert set(intents) == {"pck-REAL-1", "gem-REAL-2"}


def test_other_events_and_bad_lines_ignored(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text(
        "not json\n\n"
        + json.dumps({"event": "scan", "intent": {"client_order_id": "x"}}) + "\n"
        + json.dumps({"event": "trade_intent",
                      "intent": {"client_order_id": "man-1"}}) + "\n",
        encoding="utf-8",
    )
    assert set(daily_reconcile._load_intents(str(log))) == {"man-1"}


def test_missing_log_is_empty(tmp_path):
    assert daily_reconcile._load_intents(str(tmp_path / "nope.jsonl")) == {}
