"""The same deal-collapse test, on a memory layer that needs no Docker.

    python scripts/mem0_probe.py

PROTOTYPE, NOT A COMPONENT. Own venv, own worktree, imported by nothing.

WHY THIS EXISTS
Graphiti passed the temporal test but arrived with three bills: ~31s per
headline to ingest, a graph database server (its embedded backend is dead —
Kuzu's extension CDN is offline), and a model that will not share 16GB of VRAM
with the one that does the gating.

Mem0 takes the other approach. Rather than a graph with validity windows, it
asks the model whether each new fact ADDs to, UPDATEs, or DELETEs what is
already stored. Chroma persists to a plain directory, so there is no service
to run and nothing to keep alive between reboots.

THE SAME CASE, SO THE ANSWERS COMPARE
Pfizer/Seagen announced, then called off — the case every model failed on the
hardened news benchmark. What matters is whether the memory knows the deal is
dead afterwards, and what it costs to find out.

WHAT IS LOST VERSUS GRAPHITI
Mem0 updates or deletes; it does not keep an invalidated fact with the date it
stopped being true. That is fine for "what is true now", which is the only
question the news gate asks. It is NOT enough to ask "what did we believe on
2026-09-01", which is what an honest backtest of the gate would need.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("MEM0_TELEMETRY", "False")

from mem0 import Memory

OLLAMA = "http://localhost:11434"
CHAT_MODEL = "gemma3:12b"
EMBED_MODEL = "nomic-embed-text"
STORE = Path("./tmp/mem0_probe")

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

EPISODES = (
    ("2026-08-03",
     "Pfizer announces a definitive agreement to acquire Seagen for $43 billion. "
     "Seagen shareholders will receive $229 per share in cash."),
    ("2026-09-15",
     "Pfizer and Seagen call off merger talks after regulatory pushback. "
     "The companies said the proposed acquisition has been terminated."),
)

QUESTIONS = (
    "Is Pfizer currently acquiring Seagen?",
    "What happened to the Pfizer Seagen deal?",
)


def _config(model: str) -> dict:
    return {
        "llm": {"provider": "ollama", "config": {
            "model": model, "ollama_base_url": OLLAMA, "temperature": 0.0}},
        "embedder": {"provider": "ollama", "config": {
            "model": EMBED_MODEL, "ollama_base_url": OLLAMA}},
        "vector_store": {"provider": "chroma", "config": {
            "collection_name": "news", "path": str(STORE)}},
    }


def main() -> int:
    model = os.environ.get("PROBE_MODEL", CHAT_MODEL)
    if STORE.exists():
        shutil.rmtree(STORE, ignore_errors=True)
    STORE.mkdir(parents=True, exist_ok=True)
    # mem0 keeps a history database OUTSIDE the configured vector store, in
    # ~/.mem0/history.db. Deleting only the Chroma folder leaves it behind and
    # the next run starts with memories it was never given — which is how the
    # first version of this probe reported a termination fact extracted from
    # an announcement headline. State outside the store you configured is a
    # trap worth knowing about before this ships anywhere.
    history = Path.home() / ".mem0" / "history.db"
    if history.exists():
        history.unlink()
        print(f"{DIM}cleared {history}{RESET}")

    print(f"{DIM}backend Chroma (a folder, no server) · chat {model} · "
          f"embed {EMBED_MODEL} · all local{RESET}")
    memory = Memory.from_config(_config(model))

    print(f"\n{BOLD}INGEST{RESET}")
    costs = []
    for stamp, text in EPISODES:
        started = time.perf_counter()
        result = memory.add(text, user_id="PFE", metadata={"on": stamp})
        elapsed = time.perf_counter() - started
        costs.append(elapsed)
        events = [f"{r.get('event')}:{str(r.get('memory'))[:46]}"
                  for r in (result or {}).get("results", [])]
        print(f"  {GREEN}{stamp}{RESET} {elapsed:6.1f}s  {DIM}{text[:52]}...{RESET}")
        for event in events or ["(no memory operations reported)"]:
            print(f"      {CYAN}{event}{RESET}")

    print(f"\n{BOLD}WHAT IS STORED NOW{RESET}")
    for row in (memory.get_all(filters={"user_id": "PFE"}) or {}).get("results", []):
        print(f"  - {str(row.get('memory'))[:92]}")

    print(f"\n{BOLD}RECALL{RESET}")
    for question in QUESTIONS:
        started = time.perf_counter()
        hits = (memory.search(question, filters={"user_id": "PFE"}, limit=5) or {}).get("results", [])
        elapsed = time.perf_counter() - started
        print(f"\n  {CYAN}{question}{RESET} {DIM}({elapsed:.2f}s){RESET}")
        if not hits:
            print(f"    {YELLOW}nothing recalled{RESET}")
        for hit in hits:
            print(f"    - {str(hit.get('memory'))[:88]}  "
                  f"{DIM}score {hit.get('score', 0):.3f}{RESET}")

    print(f"\n{BOLD}COST{RESET}")
    print(f"  ingestion: {sum(costs):.1f}s for {len(costs)} headlines "
          f"({sum(costs)/len(costs):.1f}s each)")
    print(f"{DIM}  graphiti needed 31.1s each and a database server.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
