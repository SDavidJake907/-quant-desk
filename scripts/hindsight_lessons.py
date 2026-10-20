"""Step 2 of Gemma's Hindsight memory (advisor/hindsight_memory.py).

Runs in the SEPARATE venv C:/Users/kitti/hindsight-sandbox (pip hindsight-all;
its dependencies are kept out of the trading venv):

    C:/Users/kitti/hindsight-sandbox/Scripts/python.exe scripts/hindsight_lessons.py

Starts Hindsight embedded (its own Postgres, local CPU embedding + reranker
models; Gemma through Ollama does the reading), retains the feed's memories it
has not seen before, then reflects: the lessons from her own trades, cited,
nothing invented. Writes data/gemma/hindsight/lessons.json for her 05:45 run.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HINDSIGHT_API_EMBEDDINGS_PROVIDER", "local")
os.environ.setdefault("HINDSIGHT_API_RERANKER_PROVIDER", "local")
# 2026-10-20: the 05:05 run failed -- Hindsight gives each reflect LLM call 30s and
# the whole reflect 300s, and a cold Gemma (loading into the GPU) blew both. A local
# 12B needs minutes, not seconds: per call 300s, whole reflect 20 minutes.
os.environ.setdefault("HINDSIGHT_API_LLM_TIMEOUT", "300")
os.environ.setdefault("HINDSIGHT_API_REFLECT_LLM_TIMEOUT", "300")
os.environ.setdefault("HINDSIGHT_API_REFLECT_WALL_TIMEOUT", "1200")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hindsight import HindsightClient, HindsightServer  # noqa: E402

DIR = Path("data/gemma/hindsight")
FEED = DIR / "feed.json"
SEEN = DIR / "retained.json"
LESSONS = DIR / "lessons.json"
BANK = "gemma-live"
MODEL = "gemma4:12b"
OLLAMA = "http://localhost:11434"
REFLECT_TRIES = 2
QUESTION = ("What lessons should Gemma take from her own trades and decisions since the challenge "
            "began, before her next morning decision? Be specific, cite her trades with dates and "
            "results, separate what is known from opinion, and do not invent anything.")


def save(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def ollama(payload: dict, timeout: float) -> None:
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def warm_up() -> None:
    """Load Gemma into the GPU before Hindsight's first call (an empty prompt only loads)."""
    t = time.monotonic()
    ollama({"model": MODEL, "prompt": "", "keep_alive": "30m"}, timeout=600)
    print(f"hindsight: {MODEL} loaded in {time.monotonic() - t:.0f}s")


def unload() -> None:
    """One model at a time: free the GPU for her 05:45 run."""
    try:
        ollama({"model": MODEL, "keep_alive": 0}, timeout=60)
    except Exception as e:                                 # noqa: BLE001 -- best effort
        print(f"hindsight: unload failed: {e}")


def reflect(client) -> str:
    for attempt in range(1, REFLECT_TRIES + 1):
        try:
            text = client.reflect(bank_id=BANK, query=QUESTION, budget="mid").text
            if text and text.strip():
                return text
            print(f"hindsight: reflect attempt {attempt} returned nothing")
        except Exception as e:                             # noqa: BLE001 -- retried, then raised
            print(f"hindsight: reflect attempt {attempt} failed: {str(e)[:200]}")
            if attempt == REFLECT_TRIES:
                raise
    raise RuntimeError("reflect returned no lessons")


def main() -> int:
    warm_up()
    try:
        return run()
    finally:
        unload()


def run() -> int:
    feed = json.loads(FEED.read_text(encoding="utf-8"))
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()
    new = [m for m in feed if m["id"] not in seen]
    started = time.monotonic()
    with (HindsightServer(llm_provider="ollama", llm_model=MODEL, log_level="warning") as server,
          HindsightClient(base_url=server.url) as client):
        try:
            client.create_bank(bank_id=BANK, name="Gemma (live)",
                               mission="Remember a junior stock trader's decisions, trades and results.")
        except Exception:                                  # noqa: BLE001 -- already exists
            pass
        for m in new:
            ts = m["at"][:19]
            client.retain(bank_id=BANK, content=m["text"], timestamp=ts, context=f"gemma {m['kind']}",
                          document_id=m["id"])
            seen.add(m["id"])
            save(SEEN, sorted(seen))
        text = reflect(client)
    lessons = {"at": datetime.now().astimezone().isoformat(), "text": text, "memories": len(feed),
               "new": len(new), "seconds": round(time.monotonic() - started)}
    save(LESSONS, lessons)
    print(f"hindsight: {len(new)} new of {len(feed)} memories; lessons {len(text)} chars; "
          f"{lessons['seconds']}s\n{text[:1500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
