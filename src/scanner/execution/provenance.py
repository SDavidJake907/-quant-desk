"""Which build produced this decision.

WHY A POLICY FINGERPRINT IS NOT ENOUGH
`TradingPolicy.fingerprint` answers "which RULES ran". It cannot answer "which
CODE ran", and the two come apart exactly where it matters most.

On 2026-08-18 the intraday parser was returning the bar still forming, so live
scans were triggering on a bucket three minutes old with twenty-seven minutes
left to change — while the backtest measured entry at the completed bar's
close. Fixing it changed live behaviour materially and changed no policy field
at all. Trades either side of that fix carry the same fingerprint and are not
the same experiment.

The same is true of the adjusted-price fix, of `drop_incomplete`, and of any
future change to the data path. So every audit record carries a build id as
well, and an evaluation window is defined by BOTH.

Nothing here fails a run. A missing git binary or a build from a tarball
degrades to "unknown" rather than stopping trading over a provenance detail.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

UNKNOWN = "unknown"

_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def build_id() -> str:
    """Short commit SHA, with `+dirty` when the tree has uncommitted changes.

    Cached: this is read on every audit record and the answer cannot change
    within one process.

    `+dirty` matters more than it looks. A run from an edited working tree is
    not reproducible from any commit, and a window containing dirty builds
    cannot be replayed — which is the whole point of recording this.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return UNKNOWN
    if not sha:
        return UNKNOWN
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return sha
    return f"{sha}+dirty" if dirty else sha
