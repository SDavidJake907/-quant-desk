"""Take the operator's picks off for a weekend, and put them back.

    python scripts/picks_pause.py --pause            # save picks.txt, empty it, sell now
    python scripts/picks_pause.py --resume           # restore the saved list (bought at the next sync)

Operator, 2026-10-08: "Tomorrow I would like to close all my positions, resume
on Monday." Only the picks book: Gemma's book is hers. Run by two one-time tasks
(stock-scanner-picks-pause Fri 11:25 AK, stock-scanner-picks-resume Mon 05:20 AK).

--pause  copies watchlists/picks.txt to watchlists/picks.paused.txt, leaves
         picks.txt with its comments and no names, commits, and runs the normal
         sync with --submit: the list is now empty, so every pick is sold
         (stop legs cancelled first), exactly as removing a name always does.
--resume copies the saved list back and commits; the next 30-minute sync buys
         the names with fresh stops. Refuses if there is nothing saved.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

LIST = Path("watchlists/picks.txt")
SAVED = Path("watchlists/picks.paused.txt")


def emptied(text: str, note: str) -> str:
    """The list's comment lines kept, every name removed, a note added."""
    kept = [line for line in text.splitlines() if line.strip().startswith("#") or not line.strip()]
    return "\n".join(kept + [f"# {note}"]) + "\n"


def commit(message: str, *paths: Path) -> None:
    subprocess.run(["git", "add", "--", *map(str, paths)], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message, "--", *map(str, paths)], check=False)


def pause() -> int:
    if SAVED.exists():
        print(f"FAILED: {SAVED} already exists -- a pause is in progress; resume first")
        return 1
    shutil.copyfile(LIST, SAVED)
    LIST.write_text(emptied(LIST.read_text(encoding="utf-8"),
                            f"{date.today()}: PAUSED for the weekend (picks_pause.py); "
                            f"names saved in {SAVED.name}, restored Monday"), encoding="utf-8")
    commit(f"chore: picks paused for the weekend ({date.today()})", LIST, SAVED)
    print(f"picks paused; saved to {SAVED}; selling now")
    return subprocess.run([sys.executable, "scripts/picks_sync.py", "--submit"]).returncode


def resume() -> int:
    if not SAVED.exists():
        print(f"FAILED: nothing to resume -- {SAVED} not found")
        return 1
    shutil.copyfile(SAVED, LIST)
    SAVED.unlink()
    commit(f"chore: picks resumed ({date.today()})", LIST, SAVED)
    print(f"picks restored from {SAVED.name}; the next sync buys them")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    a = p.parse_args()
    raise SystemExit(pause() if a.pause else resume())
