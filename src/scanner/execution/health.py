"""Is the news gate actually running?

THE FAILURE THIS EXISTS TO MAKE LOUD
`news_gate` deliberately fails open: a model outage must never stop the
deterministic strategy. The cost of that choice is that with Ollama down the
gate approves EVERYTHING, including the takeover targets and structural-news
names it exists to refuse.

    if not self.checked:
        return Verdict(allowed=True, reason="news not checked")

`trade.py` prints a verdict's reason only when the verdict REFUSES. This one
allows, so the string is never printed, never logged, and never written to the
audit. Grepping the whole scheduled log for "not checked" returns zero hits,
and always would. A session running without one of its safety checks looked
byte-identical to a healthy one.

Probing once per run, before any candidate exists, is what makes it visible on
quiet days too — a scan that finds nothing never calls the gate at all, so
per-candidate logging alone would still miss an outage.

This reports. It does not block: turning a model outage into a trading halt is
a policy decision, and it belongs in `TradingPolicy` with a measurement behind
it, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from scanner.config import settings


@dataclass(frozen=True)
class ModelHealth:
    """Whether the triage model could be reached, and whether it is loaded."""

    reachable: bool
    model_present: bool
    model: str

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_present

    def banner(self) -> str:
        """One line for the scheduled log. Single-line on purpose: this has to
        survive being grepped out of a file nobody reads until something is
        wrong."""
        if self.ok:
            return f"news gate OK — {self.model} reachable"
        if not self.reachable:
            return (f"NEWS GATE FAILING OPEN — Ollama unreachable, {self.model} "
                    f"absent; every name will pass the news check unrefused")
        return (f"NEWS GATE FAILING OPEN — Ollama up but {self.model} not "
                f"loaded; every name will pass the news check unrefused")


async def probe(client: httpx.AsyncClient, model: str | None = None) -> ModelHealth:
    """Ask Ollama what it has. Never raises — a health check that can take the
    scan down with it is worse than no health check."""
    wanted = model or settings.triage_model
    try:
        response = await client.get(f"{settings.ollama_host}/api/tags", timeout=10.0)
        response.raise_for_status()
        names = {m.get("name", "") for m in (response.json().get("models") or [])}
    except Exception:
        return ModelHealth(reachable=False, model_present=False, model=wanted)
    return ModelHealth(reachable=True, model_present=wanted in names, model=wanted)
