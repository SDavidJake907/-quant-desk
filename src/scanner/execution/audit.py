"""Small append-only audit log for paper-trading operations."""

from __future__ import annotations

import json
from pathlib import Path

from scanner.config import settings
from scanner.models import AuditEvent


def append_event(path: str | Path, event: AuditEvent) -> None:
    """Append one JSON object, creating the log directory if needed.

    A record that does not name its model is stamped with the configured one.
    """
    if not event.model:
        event = event.model_copy(update={"model": settings.triage_model})
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
        handle.write("\n")


def submitted_levels(path: str | Path) -> dict[str, tuple[float, float]]:
    """The (stop, target) each symbol was last submitted with.

    The log is the only surviving record of those numbers once the broker has
    cancelled the legs that held them — which is exactly the state a `day`
    bracket leaves behind after the close. Re-deriving a stop from today's
    chart instead would silently move the exit: `size_position` chose the
    share count FROM the original stop distance, so a different stop means
    the position is no longer risking what it was built to risk.

    Takes the LAST submission per symbol. A name re-entered after a stop-out
    is a different trade at different levels, and the most recent submitted
    order is the one that produced the shares now held.
    """
    levels: dict[str, tuple[float, float]] = {}
    log = Path(path)
    if not log.exists():
        return levels
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue      # a torn final line beats losing the whole log
        if row.get("event") != "order_submitted":
            continue
        intent = row.get("intent") or {}
        symbol = str(row.get("symbol", ""))
        stop, target = intent.get("stop_price"), intent.get("target_price")
        if symbol and stop and target:
            levels[symbol] = (float(stop), float(target))
    return levels
