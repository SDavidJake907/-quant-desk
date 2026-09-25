"""Does a temporal memory fix the thing a stateless model gets wrong?

    python scripts/graphiti_probe.py

PROTOTYPE, NOT A COMPONENT. Nothing in the scanner imports this. It runs in
its own venv (`.venv-graphiti`) on its own worktree, because graphiti-core
pulls a large dependency tree and the trading venv must not move mid-window.

THE QUESTION
The hardened benchmark found every model failing the same way: state, not
reading. gemma3:12b called Pfizer an `acquirer` off "call off merger talks",
because a stateless classifier sees merger vocabulary and classifies merger.
It has no way to know a deal is dead.

Graphiti claims to fix exactly this — facts carry a validity window and are
invalidated rather than deleted when superseded. This feeds it the deal and
then the collapse, in order, and asks what is true afterwards.

WHAT IS BEING MEASURED
1. Does invalidation actually happen, on local models, without cloud keys?
2. What does ingestion COST? That is the adoption question. Triage today is
   about 1s per batch of 12 headlines. If a graph write costs many seconds of
   LLM time per headline, the answer is no regardless of how good the recall
   is, because the scanner runs every 30 minutes over 501 symbols.

RESULT, 2026-09-24 — IT WORKS, AND THE MODEL CHOICE DECIDES IT

    model                  invalidation        ingest/headline   recall
    gemma3:12b             FAILED entirely      8.0s             2-10s
    qwen2.5:14b-instruct   correct             31.1s             0.09s

With gemma3:12b the feature does not happen at all. Both episodes ingest, the
entities come out right, and then: valid_at, invalid_at and expired_at are all
None, the acquisition fact is stored TWICE, and the termination episode
produces no fact whatsoever. Nothing is contradicted because nothing was
temporal in the first place.

With qwen2.5:14b-instruct it is exactly what was advertised:

    "Pfizer announces an agreement to acquire Seagen"   INVALIDATED 2026-09-15
    "Seagen announces being acquired by Pfizer"         INVALIDATED 2026-09-15
    "Pfizer and Seagen call off merger talks"           valid from 2026-09-15
    "Seagen shareholders will receive $229 per share"   valid from 2026-08-03

So asking "is Pfizer currently acquiring Seagen" returns the acquisition with
an explicit invalidation date attached. That is precisely the PFE case the
hardened benchmark caught every model failing.

THE COSTS ARE THE PROBLEM, NOT THE CAPABILITY

1. INGESTION IS ~31s PER HEADLINE. Triage does twelve in about one second.
   Ingesting inline would blow the scan task's 25-minute ExecutionTimeLimit at
   roughly fifty headlines. Ingestion CANNOT live in the scan path.

   But RECALL is 0.09s, which is free. So the shape that works is to ingest
   out of band — the nightly DATA window already exists for exactly this kind
   of job — and only query during a scan.

2. THE TWO JOBS WANT DIFFERENT MODELS. qwen2.5:14b is the one that can build
   the graph, and it is also the one that WRONGLY BLOCKED International Paper
   on the news-gate benchmark. gemma3:12b gates better and cannot build a
   graph at all.

   8.1GB + 9.0GB does not fit in 16GB, so they cannot both stay resident.
   Running both means a ~28s model load on every switch.

3. IT NEEDS A DATABASE SERVER. The embedded Kuzu backend is not merely
   deprecated — its extension CDN is offline, so INSTALL FTS fails and the
   backend cannot function at all. FalkorDB works, and it means Docker Desktop
   running permanently on a host that was off for thirty days last month.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta

# Telemetry is on by default and this box is meant to be offline.
os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"

from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.nodes import EpisodeType

OLLAMA = "http://localhost:11434/v1"
CHAT_MODEL = "qwen2.5:14b-instruct"
EMBED_MODEL = "nomic-embed-text"

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

#: The story in order. This is the PFE benchmark case, played out over time
#: instead of compressed into one headline.
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


def _client() -> Graphiti:
    config = LLMConfig(
        api_key="ollama",          # Ollama ignores it; the client requires one
        base_url=OLLAMA,
        model=CHAT_MODEL,
        small_model=CHAT_MODEL,
    )
    return Graphiti(
        graph_driver=FalkorDriver(host="localhost", port=6379),
        llm_client=OpenAIGenericClient(config=config),
        embedder=OpenAIEmbedder(config=OpenAIEmbedderConfig(
            api_key="ollama", base_url=OLLAMA, embedding_model=EMBED_MODEL,
            embedding_dim=768,
        )),
    )


async def main() -> int:
    graphiti = _client()
    print(f"{DIM}backend FalkorDB (docker) · chat {CHAT_MODEL} · "
          f"embed {EMBED_MODEL} · all local{RESET}")

    try:
        await graphiti.build_indices_and_constraints()
    except Exception as exc:                       # noqa: BLE001 - probe
        print(f"{YELLOW}index build said: {exc}{RESET}")

    print(f"\n{BOLD}INGEST{RESET}")
    costs = []
    for stamp, text in EPISODES:
        when = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
        started = time.perf_counter()
        try:
            await graphiti.add_episode(
                name=f"news-{stamp}",
                episode_body=text,
                source=EpisodeType.text,
                source_description="headline",
                reference_time=when,
            )
        except Exception as exc:                   # noqa: BLE001 - probe
            print(f"  {RED}{stamp} failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}{RESET}")
            return 1
        elapsed = time.perf_counter() - started
        costs.append(elapsed)
        print(f"  {GREEN}{stamp}{RESET} ingested in {elapsed:6.1f}s  "
              f"{DIM}{text[:58]}...{RESET}")

    print(f"\n{BOLD}RECALL{RESET}")
    for question in QUESTIONS:
        started = time.perf_counter()
        results = await graphiti.search(question, num_results=5)
        elapsed = time.perf_counter() - started
        print(f"\n  {CYAN}{question}{RESET}  {DIM}({elapsed:.2f}s){RESET}")
        if not results:
            print(f"    {YELLOW}nothing recalled{RESET}")
        for edge in results:
            invalid = getattr(edge, "invalid_at", None)
            valid = getattr(edge, "valid_at", None)
            state = (f"{RED}INVALIDATED {invalid:%Y-%m-%d}{RESET}" if invalid
                     else f"{GREEN}still valid{RESET}")
            since = f" from {valid:%Y-%m-%d}" if valid else ""
            print(f"    - {edge.fact[:88]}")
            print(f"      {state}{since}")

    print(f"\n{BOLD}COST{RESET}")
    print(f"  ingestion: {sum(costs):.1f}s for {len(costs)} headlines "
          f"({sum(costs)/len(costs):.1f}s each)")
    print(f"{DIM}  triage today is ~1s per batch of TWELVE headlines.{RESET}")
    await graphiti.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
