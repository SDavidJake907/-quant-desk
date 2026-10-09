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
