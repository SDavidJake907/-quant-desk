"""Per-task logs read back as one timeline, so no task can lock another out."""

from __future__ import annotations

from pathlib import Path

from scanner.ops import runlog

SCAN = """preamble with no header
===== Tue 10/06/2026 11:33:02.31 =====
every open position has a live stop.
===== Tue 10/06/2026 12:03:02.31 =====
nothing has aged out
"""
MOMENTUM = """===== MOMENTUM Tue 10/06/2026 11:40:00.10 =====
tranche 0 already rebuilt
"""
FLATTEN = """===== FLATTEN Tue 10/06/2026  8:45:00.93 =====
not the closing run
"""


def test_headers_parse_with_and_without_a_task_name():
    assert runlog.header_time("===== Tue 10/06/2026 11:33:02.31 =====").hour == 11
    assert runlog.header_time("===== FLATTEN Tue 10/06/2026  8:45:00.93 =====").hour == 8
    assert runlog.header_time("===== TRADINGAGENTS live Tue 10/06/2026 05:36:01.65 =====")
    assert runlog.header_time("ordinary output") is None


def test_files_merge_into_start_time_order_and_drop_preamble():
    lines = runlog.merge([SCAN, MOMENTUM, FLATTEN])
    heads = [l for l in lines if l.startswith("=====")]
    assert [h.split()[1] for h in heads] == ["FLATTEN", "Tue", "MOMENTUM", "Tue"]
    assert "preamble with no header" not in lines
    assert lines[lines.index(heads[2]) + 1] == "tranche 0 already rebuilt"


def test_ansi_is_stripped():
    text = "===== Tue 10/06/2026 11:33:02.31 =====\n\x1b[32mok\x1b[0m\n"
    assert runlog.merge([text])[1] == "ok"


def test_read_logs_reads_every_task_file(tmp_path: Path):
    (tmp_path / "scheduled_scan.log").write_text(SCAN, encoding="utf-8")
    (tmp_path / "scheduled_momentum.log").write_text(MOMENTUM, encoding="utf-8")
    (tmp_path / "scheduled.log").write_text(FLATTEN, encoding="utf-8")   # old shared log
    (tmp_path / "other.log").write_text(MOMENTUM, encoding="utf-8")       # not a run log
    heads = [l for l in runlog.read_logs(tmp_path) if l.startswith("=====")]
    assert len(heads) == 4
