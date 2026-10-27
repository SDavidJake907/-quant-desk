"""A weekend pause keeps the list's comments and removes every name."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import picks_pause  # noqa: E402

from scanner.execution import picks  # noqa: E402


def test_emptied_list_keeps_comments_and_reads_as_no_names(tmp_path):
    text = "# PICKS -- header\n# a note\nAMD\nMRVL\n\n# 2026-10-08: added\nNVDA\n"
    out = picks_pause.emptied(text, "PAUSED for the weekend")
    path = tmp_path / "picks.txt"
    path.write_text(out, encoding="utf-8")
    assert picks.read_list(path)[0] == ()
    assert "# PICKS -- header" in out and "PAUSED for the weekend" in out


def test_keep_best_ranks_picks_by_return_since_entry():
    positions = [{"symbol": "PLTR", "unrealized_plpc": "0.062"},
                 {"symbol": "AMD", "unrealized_plpc": "-0.05"},
                 {"symbol": "ABBV", "unrealized_plpc": "0.021"},
                 {"symbol": "XOM", "unrealized_plpc": "0.011"},
                 {"symbol": "MPC", "unrealized_plpc": "0.053"}]          # Gemma's, not a pick
    picks_held = {"PLTR", "AMD", "ABBV", "XOM"}
    assert picks_pause.best(positions, picks_held, 3) == ("PLTR", "ABBV", "XOM")


def test_emptied_list_can_keep_some_names(tmp_path):
    text = "# header\nAMD\nPLTR\nXOM\n"
    out = picks_pause.emptied(text, "PAUSED", keep=("PLTR",))
    path = tmp_path / "picks.txt"
    path.write_text(out, encoding="utf-8")
    assert picks.read_list(path)[0] == ("PLTR",)
