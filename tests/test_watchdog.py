"""The supervisor, which nothing had ever tested.

A watchdog is only useful in the minutes when something else is already
broken, which is the worst possible time to discover it does not work. These
pin the three properties the data job is about to depend on:

  - it refuses to supervise anything that can reach the broker
  - a clean exit is not retried
  - a failing command is retried a BOUNDED number of times and then gives up

The refusal is the important one. A process that may have submitted an order
and died before reading the response has unknown broker state; restarting it
can double a position. Reconciliation has to happen first, and a supervisor
cannot do that, so it declines the job instead of guessing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts_watchdog import run  # see conftest shim


def _events(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _python(*code: str) -> list[str]:
    return [sys.executable, "-c", "; ".join(code)]


class TestItRefusesTheBroker:
    def test_submit_paper_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="refuses"):
            run(["python", "trade.py", "--submit-paper"], max_restarts=1,
                backoff=0, max_backoff=0, log_path=tmp_path / "w.jsonl")

    def test_live_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="refuses"):
            run(["python", "trade.py", "--live"], max_restarts=1,
                backoff=0, max_backoff=0, log_path=tmp_path / "w.jsonl")

    def test_a_read_only_command_is_accepted(self, tmp_path):
        code = run(_python("pass"), max_restarts=0, backoff=0, max_backoff=0,
                   log_path=tmp_path / "w.jsonl")
        assert code == 0

    def test_an_empty_command_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            run([], max_restarts=1, backoff=0, max_backoff=0,
                log_path=tmp_path / "w.jsonl")


class TestRestartPolicy:
    def test_a_clean_exit_is_not_retried(self, tmp_path):
        log = tmp_path / "w.jsonl"
        assert run(_python("pass"), max_restarts=3, backoff=0, max_backoff=0,
                   log_path=log) == 0
        starts = [e for e in _events(log) if e["event"] == "started"]
        assert len(starts) == 1, "a successful run must not be restarted"

    def test_a_failure_is_retried_up_to_the_limit_then_gives_up(self, tmp_path):
        """Bounded, because an unbounded restart loop against a provider that
        is rate-limiting you is indistinguishable from an attack."""
        log = tmp_path / "w.jsonl"
        code = run(_python("import sys", "sys.exit(3)"), max_restarts=2,
                   backoff=0, max_backoff=0, log_path=log)
        assert code == 3
        events = _events(log)
        starts = [e for e in events if e["event"] == "started"]
        assert len(starts) == 3, "one attempt plus two restarts"
        assert any(e["event"] == "restart_limit_reached" for e in events)

    def test_the_failing_exit_code_is_propagated(self, tmp_path):
        """The scheduled task reports success or failure from this code.
        swallowing it would make a dead job look healthy in Task Scheduler."""
        code = run(_python("import sys", "sys.exit(7)"), max_restarts=0,
                   backoff=0, max_backoff=0, log_path=tmp_path / "w.jsonl")
        assert code == 7

    def test_a_command_that_fails_then_would_succeed_still_records_the_crash(self, tmp_path):
        log = tmp_path / "w.jsonl"
        run(_python("import sys", "sys.exit(1)"), max_restarts=1, backoff=0,
            max_backoff=0, log_path=log)
        assert any(e["event"] == "crashed" for e in _events(log))


class TestLogging:
    def test_the_log_is_created_and_append_only_json_lines(self, tmp_path):
        log = tmp_path / "nested" / "w.jsonl"
        run(_python("pass"), max_restarts=0, backoff=0, max_backoff=0, log_path=log)
        assert log.exists(), "the watchdog must create its own log directory"
        for event in _events(log):
            assert "at" in event and "event" in event
