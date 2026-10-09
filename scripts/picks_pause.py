"""Take the operator's picks off for a weekend, and put them back.

    python scripts/picks_pause.py --pause                  # save picks.txt, empty it, sell now
    python scripts/picks_pause.py --pause --keep-best 3    # ...but keep the 3 best by return
    python scripts/picks_pause.py --resume                 # restore the saved list

Operator, 2026-10-08: "Tomorrow I would like to close all my positions, resume
on Monday." 2026-10-09: Gemma holds her 3 over the weekend, so "hold the 3 best
position, head to head with Gemma". Only the picks book: Gemma's book is hers.
Run by one-time tasks (stock-scanner-picks-pause Fri 11:25 AK,
stock-scanner-picks-resume Mon 05:20 AK).

--pause  copies watchlists/picks.txt to watchlists/picks.paused.txt, leaves
         picks.txt with its comments and only the kept names (none, or the
         --keep-best N picks with the highest return since entry at that
         moment), commits, and runs the normal sync with --submit: every name
         no longer listed is sold (stop legs cancelled first), exactly as
         removing a name always does. Kept names keep their positions and stops.
--resume copies the saved list back and commits; the next 30-minute sync buys
         the names not held, with fresh stops. Refuses if there is nothing saved.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

LIST = Path("watchlists/picks.txt")
SAVED = Path("watchlists/picks.paused.txt")


def emptied(text: str, note: str, keep: Iterable[str] = ()) -> str:
    """The list's comment lines kept, every name removed except `keep`, a note added."""
    keep = tuple(keep)
    kept = [line for line in text.splitlines() if line.strip().startswith("#") or not line.strip()]
    return "\n".join(kept + [f"# {note}"] + list(keep)) + "\n"


def best(positions: Iterable[Mapping], picks_held: set[str], n: int) -> tuple[str, ...]:
    """The `n` picks with the highest return since entry (unrealized_plpc)."""
    mine = [p for p in positions if p.get("symbol") in picks_held]
    mine.sort(key=lambda p: float(p.get("unrealized_plpc") or 0), reverse=True)
    return tuple(p["symbol"] for p in mine[:n])


async def best_picks(n: int) -> tuple[str, ...]:
    import httpx
    from scanner.config import settings
    from scanner.execution import ownership
    from scanner.execution.alpaca import Alpaca
    from scanner.execution.ownership import PICKS
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        owners = ownership.owners(await broker._get("/v2/orders", status="all", limit=500,
                                                    nested="true"))
    held = {p["symbol"] for p in positions if ownership.owner(p["symbol"], owners) == PICKS}
    for p in sorted(positions, key=lambda p: -float(p.get("unrealized_plpc") or 0)):
        if p["symbol"] in held:
            print(f"  {p['symbol']:<6} {float(p['unrealized_plpc']) * 100:+.2f}% since entry")
    return best(positions, held, n)


def commit(message: str, *paths: Path) -> None:
    subprocess.run(["git", "add", "--", *map(str, paths)], check=True)
    subprocess.run(["git", "commit", "-q", "-m", message, "--", *map(str, paths)], check=False)


def pause(keep_best: int = 0) -> int:
    if SAVED.exists():
        print(f"FAILED: {SAVED} already exists -- a pause is in progress; resume first")
        return 1
    keep = asyncio.run(best_picks(keep_best)) if keep_best else ()
    shutil.copyfile(LIST, SAVED)
    note = (f"{date.today()}: PAUSED for the weekend (picks_pause.py); "
            + (f"kept the {len(keep)} best: {', '.join(keep)}; " if keep else "")
            + f"names saved in {SAVED.name}, restored Monday")
    LIST.write_text(emptied(LIST.read_text(encoding="utf-8"), note, keep), encoding="utf-8")
    commit(f"chore: picks paused for the weekend ({date.today()})"
           + (f", keeping {', '.join(keep)}" if keep else ""), LIST, SAVED)
    print(f"picks paused; saved to {SAVED}; keeping {list(keep) or 'nothing'}; selling the rest now")
    return subprocess.run([sys.executable, "scripts/picks_sync.py", "--submit"]).returncode


def resume() -> int:
    if not SAVED.exists():
        print(f"FAILED: nothing to resume -- {SAVED} not found")
        return 1
    shutil.copyfile(SAVED, LIST)
    SAVED.unlink()
    commit(f"chore: picks resumed ({date.today()})", LIST, SAVED)
    print(f"picks restored from {SAVED.name}; the next sync buys what is not held")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    p.add_argument("--keep-best", type=int, default=0,
                   help="with --pause: keep the N picks with the best return since entry")
    a = p.parse_args()
    raise SystemExit(pause(a.keep_best) if a.pause else resume())
