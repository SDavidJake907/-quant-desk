"""Does recalled memory change what the gate decides?

    python scripts/gate_with_memory.py

This is the only question that matters. Recall being fast and cheap is
irrelevant if the model still gets the call wrong with the memory in front of
it. So this replays benchmark cases through the SAME model twice — once blind,
once with what the memory layer recalled — and compares the verdicts.

Run scripts/mem0_probe.py first; this reads the store it builds.

RESULT, 2026-09-24 — MEMORY DID NOT HELP, AND ONCE IT HURT

    model                  blind      with memory
    gemma3:12b             acquirer   acquirer      no change, both wrong
    qwen2.5:14b-instruct   none       acquirer      CORRECT -> WRONG

Two models, one case, so this is suggestive rather than settled. But the
direction is the opposite of the hypothesis, and one of the two got WORSE.

Why: Mem0 does not invalidate. Both facts are stored flat and both come back,
and on the query "is Pfizer acquiring Seagen" the ANNOUNCEMENT ranks above the
termination (0.778 vs 0.675). So the context handed to the model leads with a
dead deal stated as fact. qwen answered correctly from the headline alone and
was then talked out of it by its own memory.

This is the project's recorded pattern appearing again: seven of nine context
gates made the holdout worse. Adding context to this decision has a history of
hurting, and that history now includes retrieval memory.

WHAT IT DOES NOT SHOW
It does not show memory is useless here. It shows FLAT memory is. Graphiti
marked the same acquisition INVALIDATED with a date, so its context would say
"this was true until 2026-09-15" rather than asserting it. That distinction is
the whole value, and it is the expensive half — a database server and a model
that will not share 16GB with the gate.

So the next experiment is not a different memory library. It is whether
invalidation-aware context changes a decision at all. If it does not, nothing
in this direction is worth a window.

NOTE FOR AN OFFLINE BOX
mem0 emitted PostHog telemetry despite ANONYMIZED_TELEMETRY and MEM0_TELEMETRY
being set. Anything shipped from this would need that properly severed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from mem0 import Memory

OLLAMA = "http://localhost:11434"
CHAT_MODEL = os.environ.get("PROBE_MODEL", "gemma3:12b")
EMBED_MODEL = "nomic-embed-text"
STORE = Path("./tmp/mem0_probe")

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

HEADLINE = "Pfizer and Seagen call off merger talks after regulatory pushback"
EXPECTED = "none"

SCHEMA = {
    "type": "object",
    "properties": {"deal_role": {"type": "string",
                                 "enum": ["acquirer", "target", "none"]}},
    "required": ["deal_role"],
}


def _config() -> dict:
    return {
        "llm": {"provider": "ollama", "config": {
            "model": CHAT_MODEL, "ollama_base_url": OLLAMA, "temperature": 0.0}},
        "embedder": {"provider": "ollama", "config": {
            "model": EMBED_MODEL, "ollama_base_url": OLLAMA}},
        "vector_store": {"provider": "chroma", "config": {
            "collection_name": "news", "path": str(STORE)}},
    }


def ask(prompt: str) -> str:
    body = json.dumps({
        "model": CHAT_MODEL, "stream": False, "format": SCHEMA,
        "options": {"temperature": 0.0},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        content = json.loads(resp.read())["message"]["content"]
    return str(json.loads(content).get("deal_role", "")).lower()


def main() -> int:
    if not STORE.exists():
        print(f"{RED}no store at {STORE} — run scripts/mem0_probe.py first{RESET}")
        return 2

    memory = Memory.from_config(_config())
    recalled = [str(h.get("memory")) for h in
                (memory.search("Pfizer Seagen acquisition status",
                               filters={"user_id": "PFE"}, limit=5)
                 or {}).get("results", [])]

    print(f"{DIM}model {CHAT_MODEL} · {len(recalled)} memories recalled{RESET}")
    for item in recalled:
        print(f"  {DIM}- {item[:86]}{RESET}")

    base = (f"Headline about Pfizer (PFE):\n\n{HEADLINE}\n\n"
            "deal_role: is PFE being ACQUIRED (target), doing the acquiring "
            '(acquirer), or is this not a deal at all (none)? Litigation, '
            'dividends and analyst notes are "none".')

    context = "\n".join(f"- {m}" for m in reversed(recalled))
    withmem = (f"What is already known about PFE, oldest first:\n{context}\n\n"
               f"{base}")

    print(f"\n{BOLD}VERDICT{RESET}  {DIM}(expected '{EXPECTED}'){RESET}")
    for label, prompt in (("blind", base), ("with memory", withmem)):
        got = ask(prompt)
        ok = got == EXPECTED
        mark = f"{GREEN}CORRECT{RESET}" if ok else f"{RED}WRONG{RESET}"
        print(f"  {label:<13} deal_role={got or '-':<9} {mark}")

    print(f"\n{DIM}'target' is the only verdict that blocks a trade, so a wrong "
          f"'acquirer' costs nothing operationally — but it is still the model "
          f"believing a dead deal is live.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
