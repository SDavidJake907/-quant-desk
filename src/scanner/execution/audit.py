"""Small append-only audit log for paper-trading operations."""

from __future__ import annotations

import json
from pathlib import Path

from scanner.models import AuditEvent


def append_event(path: str | Path, event: AuditEvent) -> None:
    """Append one JSON object, creating the log directory if needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
        handle.write("\n")
