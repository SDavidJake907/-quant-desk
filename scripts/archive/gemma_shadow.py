"""Shadow Gemma: ask live Gemma's exact question again with variants (rules in
scanner/advisor/gemma_shadow.py). Never trades.

    python scripts/gemma_shadow.py          # today's submitted question, if not done
    python scripts/gemma_shadow.py --any    # the newest question, even a preview

Runs after gemma_trade.py in the 05:45 AK task (own log). Appends one record
per variant to data/gemma/shadow.jsonl; selection_grade.py grades them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_shadow as gsh
from scanner.advisor import gemma_trader as gt
from scanner.config import settings
from scanner.execution.policy import TradingPolicy

QUESTIONS = Path("data/gemma/questions")
OUT = Path("data/gemma/shadow.jsonl")
POLICY = TradingPolicy()
MENU = gt.Menu(stops=tuple(k for k, _ in POLICY.gemma_stop_atr_menu),
               targets=tuple(k for k, _ in POLICY.gemma_target_r_menu),
               sizes=tuple(k for k, _ in POLICY.gemma_size_risk_menu))
#: Thinking adds its own tokens before the answer; room for them.
THINK_CTX = 32768
#: Unlimited, gemma4:12b's thinking looped ("Wait, I need to check...") for
#: 17,400 tokens until the context ran out and never answered (2026-10-19).
#: So: think within a budget, then answer from those notes with thinking off.
THINK_BUDGET = 4096
NOTES_CHARS = 8000
TIMEOUT_S = 900
VOTE_TEMPERATURE = 0.7
VOTE_SEEDS = (1, 2, 3)


async def _chat(client, payload: dict) -> dict:
    r = await client.post(f"{settings.ollama_host}/api/chat", json=payload, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


async def ask(client, text: str, schema: dict, *, temperature: float,
              seed: int | None = None, num_ctx: int | None = None) -> dict | None:
    """One grammar-constrained answer, thinking off as live; None on failure."""
    options = {"temperature": temperature, "num_ctx": num_ctx or settings.triage_num_ctx}
    if seed is not None:
        options["seed"] = seed
    payload = {"model": settings.triage_model, "stream": False, "format": schema,
               "think": False, "options": options,
               "messages": [{"role": "user", "content": text}]}
    try:
        return json.loads((await _chat(client, payload)).get("message", {}).get("content", ""))
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  ! model call failed: {exc}")
        return None


async def think_then_answer(client, text: str, schema: dict) -> tuple[dict | None, int, bool]:
    """Think within THINK_BUDGET tokens, then answer from those notes.
    (answer, thinking chars, whether the thinking finished inside the budget)."""
    payload = {"model": settings.triage_model, "stream": False, "think": True,
               "options": {"temperature": 0.0, "num_ctx": THINK_CTX,
                           "num_predict": THINK_BUDGET},
               "messages": [{"role": "user", "content": text + "\n\nThink it through, "
                             "then stop: weigh each holding and each empty slot once."}]}
    try:
        body = await _chat(client, payload)
    except httpx.HTTPError as exc:
        print(f"  ! thinking call failed: {exc}")
        return None, 0, False
    message = body.get("message", {})
    notes = ((message.get("thinking") or "") + "\n" + (message.get("content") or "")).strip()
    finished = body.get("done_reason") == "stop"
    second = (text + "\n\nYour own notes from thinking this through (they may be cut off):\n"
              + notes[-NOTES_CHARS:] + "\n\nNow give your answer.")
    return await ask(client, second, schema, temperature=0.0, num_ctx=THINK_CTX), len(notes), finished


def done_ids() -> set[tuple[str, str]]:
    if not OUT.exists():
        return set()
    out = set()
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.add((r["question"], r["variant"]))
    return out


def newest_question(any_kind: bool) -> dict | None:
    files = sorted(QUESTIONS.glob("*.json")) if QUESTIONS.exists() else []
    for f in reversed(files):
        q = json.loads(f.read_text(encoding="utf-8"))
        if any_kind or (q.get("submitted") and q.get("day") == date.today().isoformat()):
            return q
    return None


def record(q: dict, variant: str, decision: gt.Decision, keep, *, schema_kept: bool,
           seconds: float, answers: int) -> gsh.ShadowRecord:
    return gsh.ShadowRecord(
        question=q["id"], day=q["day"], session=q["session"], variant=variant,
        keep=tuple(keep), live_keep=tuple(q["live_keep"]), menu=tuple(sorted(q["facts"])),
        journal=decision.journal, rejected=decision.rejected, schema_kept=schema_kept,
        seconds=round(seconds, 1), answers=answers)


async def run(q: dict, variant: str, client) -> gsh.ShadowRecord:
    held, facts, n = set(q["held"]), q["facts"], q["max_positions"]
    start = time.monotonic()
    if variant == "think":
        answer, thought, finished = await think_then_answer(client, q["text"], q["schema"])
        d = gt.validate(answer, facts, held=held, max_positions=n, menu=MENU)
        print(f"  think: {thought:,} chars of notes, "
              f"{'finished' if finished else 'cut at the budget'}")
        return record(q, variant, d, d.keep, schema_kept=gsh.kept_schema(answer, facts, MENU),
                      seconds=time.monotonic() - start, answers=1)
    decisions, kept = [], True
    for seed in VOTE_SEEDS:
        answer = await ask(client, q["text"], q["schema"], temperature=VOTE_TEMPERATURE,
                           seed=seed)
        kept &= gsh.kept_schema(answer, facts, MENU)
        decisions.append(gt.validate(answer, facts, held=held, max_positions=n, menu=MENU))
        print(f"  vote3 seed {seed}: {list(decisions[-1].keep)}")
    keep = gsh.vote(decisions, n)
    return record(q, variant, decisions[0], keep, schema_kept=kept,
                  seconds=time.monotonic() - start, answers=len(decisions))


async def main(any_kind: bool) -> int:
    q = newest_question(any_kind)
    if q is None:
        print("shadow: no submitted question today -- nothing to do")
        return 0
    done = done_ids()
    print(f"shadow: question {q['id']}; live Gemma held {q['live_keep']}")
    async with httpx.AsyncClient() as client:
        for variant in gsh.VARIANTS:
            if (q["id"], variant) in done:
                print(f"  {variant}: already answered")
                continue
            rec = await run(q, variant, client)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            with OUT.open("a", encoding="utf-8") as f:
                f.write(rec.model_dump_json() + "\n")
            same = "same as live" if set(rec.keep) == set(rec.live_keep) else "DIFFERS"
            print(f"  {variant}: holds {list(rec.keep)} ({same}); schema kept "
                  f"{rec.schema_kept}; {rec.seconds:.0f}s")
            if rec.journal:
                print(f"    journal: {rec.journal[:300]}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--any", action="store_true", help="use the newest question, even a preview")
    raise SystemExit(asyncio.run(main(p.parse_args().any)))
