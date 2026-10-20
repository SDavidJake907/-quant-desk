"""Step 1 of Gemma's Hindsight memory (advisor/hindsight_memory.py): the audit log
and her saved reviews -> data/gemma/hindsight/feed.json. Reads only.

    python scripts/hindsight_feed.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import hindsight_memory as hm
from scanner.config import settings
from scanner.execution.scorecard import read_rows

REVIEWS = Path("data/gemma/reviews")


def main() -> int:
    reviews = []
    for f in sorted(REVIEWS.glob("2026-*.json")):
        try:
            reviews.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            print(f"  skipped unreadable review {f.name}")
    mem = hm.memories(read_rows(settings.audit_log_path), reviews)
    hm.DIR.mkdir(parents=True, exist_ok=True)
    tmp = hm.FEED.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=1), encoding="utf-8")
    tmp.replace(hm.FEED)
    print(f"hindsight feed: {len(mem)} memories since {hm.SINCE} -> {hm.FEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
