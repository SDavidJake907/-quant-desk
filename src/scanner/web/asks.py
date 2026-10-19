"""Ask Gemma: the operator's questions and her answers, as an append-only log.

Each question is one line `{"id", "at", "question"}`; its answer is a second
line with the same id `{"id", "answered_at", "answer"}` (or `"error"`). The
reader merges them, newest first. A question with no answer after STALE_MIN
minutes is shown as not answered (the server restarted, or Ollama failed).

Asking trades nothing and changes no rule: it reads her briefing and review.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from scanner.advisor import gemma_review as rv
from scanner.llm.ollama import LLMError, chat_text

STALE_MIN = 15
_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load(path: Path, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Questions merged with their answers, newest first."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("id"):
            merged.setdefault(row["id"], {}).update(row)
    now = now or datetime.now().astimezone()
    out = []
    for row in merged.values():
        if "question" not in row:
            continue
        if "answer" in row:
            status = "answered"
        elif "error" in row:
            status = "failed"
        else:
            try:
                asked = datetime.fromisoformat(row["at"])
                status = "pending" if now - asked < timedelta(minutes=STALE_MIN) else "failed"
            except (KeyError, ValueError):
                status = "failed"
            if status == "failed":
                row = {**row, "error": "not answered (the dashboard restarted or Gemma was busy)"}
        out.append({**row, "status": status})
    return sorted(out, key=lambda r: r.get("at", ""), reverse=True)


def pending(rows: list[dict[str, Any]]) -> bool:
    return any(r["status"] == "pending" for r in rows)


def latest_briefing(questions_dir: Path) -> str:
    """The text of the most recent decision she was asked (the morning run)."""
    files = sorted(questions_dir.glob("*.json"))
    for f in reversed(files):
        try:
            return json.loads(f.read_text(encoding="utf-8-sig")).get("text", "")
        except (OSError, ValueError):
            continue
    return ""


def latest_review(reviews_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((reviews_dir / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def answer(path: Path, ask_id: str, question: str, *, questions_dir: Path,
                 reviews_dir: Path) -> None:
    """Answer one question in the background; one at a time on the GPU."""
    async with _lock:
        history = [(r["question"], r["answer"]) for r in reversed(load(path))
                   if r["status"] == "answered"]
        text = rv.ask_prompt(question, briefing=latest_briefing(questions_dir),
                             review=rv.review_text(latest_review(reviews_dir)),
                             history=history)
        try:
            async with httpx.AsyncClient() as client:
                reply = await chat_text(text, client, max_tokens=rv.ASK_TOKENS)
            append(path, {"id": ask_id, "answered_at": _now(), "answer": reply})
        except LLMError as exc:
            append(path, {"id": ask_id, "answered_at": _now(), "error": str(exc)[:300]})


def submit(path: Path, question: str, **dirs: Path) -> str:
    """Record the question and start answering it. Returns its id."""
    ask_id = uuid.uuid4().hex[:12]
    append(path, {"id": ask_id, "at": _now(), "question": question})
    task = asyncio.get_running_loop().create_task(answer(path, ask_id, question, **dirs))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return ask_id
