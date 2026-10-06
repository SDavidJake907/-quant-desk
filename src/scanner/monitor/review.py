"""Monitor v2: ask Gemma whether the REASON for a position is broken.

Settings (2026-10-06, operator-approved design):
  gemma4:12b, think off, temperature 0, fixed seed  -- same facts, same call
  JSON schema enforced                              -- c f k t e w, nothing else
  input: the code line (math it did not compute) + legend + headlines from the
         last 48h, max 6, newest first, de-duplicated, timestamped
  red flag (f2) stands only with a VERBATIM quote AND a material event type;
         otherwise it is downgraded to watch (f1)
Record-only. Level 3 (tighten the stop or sell half, never a full exit) is
locked behind TRIALS.md #45.

The Ollama call is made here rather than through scanner.llm.ollama.chat_json:
that module is frozen for the news gate, and this needs a seed it does not send.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta

import httpx

from scanner.monitor.codes import LEGEND

SEED = 7
HOURS, MAX_HEADS = 48, 6
MATERIAL = ("guidance_cut", "downgrade", "regulatory", "fraud", "lawsuit", "offering",
            "acquired", "halt", "earnings_miss", "management_exit")
SCHEMA = {"type": "object", "properties": {
    "c": {"type": "string", "enum": ["H", "X"]},
    "f": {"type": "string", "enum": ["0", "1", "2"]},
    "k": {"type": "string", "enum": ["1", "2", "3"]},
    "t": {"type": "string", "enum": ["none", *MATERIAL]},
    "e": {"type": "string"},
    "w": {"type": "string"}},
    "required": ["c", "f", "k", "t", "e", "w"]}

PROMPT = """You monitor one open position. Python computed every number below.
Do not compute, estimate or repeat any number.

LEGEND
{legend}

POSITION ({kind})
{line}

HEADLINES, last 48h, newest first
{heads}

Question: do these headlines show the REASON for holding is broken? Price drifting
against the position is NOT a reason: {priced}. Be reluctant: exiting on noise is
how this kind of strategy loses money.

c: H hold, X exit early.  f: 0 nothing, 1 watch, 2 red flag (material negative).
k: confidence 1 low, 2 mid, 3 high.
t: the material event behind f2 ({material}), else none.
e: a span copied VERBATIM from one headline supporting f, or "".
w: at most 10 words, no tickers, no numbers."""

_CALL = {"H": "hold", "X": "exit_early"}
_CONF = {"3": "strong", "2": "neutral", "1": "weak"}


def recent_headlines(items: Iterable[tuple[str, datetime]], *, now: datetime) -> list[str]:
    """'[YYYY-MM-DD HH:MMZ] title' for the last 48h, newest first, de-duplicated, max 6."""
    seen, out = set(), []
    for title, when in sorted(items, key=lambda x: x[1], reverse=True):
        key = " ".join(title.lower().split())
        if when < now - timedelta(hours=HOURS) or key in seen:
            continue
        seen.add(key)
        out.append(f"[{when:%Y-%m-%d %H:%M}Z] {title}")
    return out[:MAX_HEADS]


def prompt(kind: str, line: str, heads: Sequence[str], priced: str) -> str:
    return PROMPT.format(legend=LEGEND, kind=kind, line=line, priced=priced,
                         heads="\n".join(f"- {h}" for h in heads) or "(none)",
                         material=", ".join(MATERIAL))


def payload(text: str, *, model: str, num_ctx: int, keep_alive: str, think: bool | None) -> dict:
    body = {"model": model, "stream": False, "keep_alive": keep_alive, "format": SCHEMA,
            "options": {"temperature": 0, "seed": SEED, "num_ctx": num_ctx},
            "messages": [{"role": "user", "content": text}]}
    if think is not None:
        body["think"] = think
    return body


async def ask(client: httpx.AsyncClient, host: str, body: dict, *, timeout: float) -> dict:
    response = await client.post(f"{host}/api/chat", json=body, timeout=timeout)
    response.raise_for_status()
    return json.loads(response.json()["message"]["content"])


def validate(out: Mapping, heads: Sequence[str]) -> dict:
    """A red flag needs a verbatim quote and a material event, else it is a watch."""
    quote = str(out.get("e", "")).strip()
    plain = [h.split("] ", 1)[-1] for h in heads]
    grounded = bool(quote) and any(quote in h for h in plain)
    result = {**out, "grounded": grounded or not quote}
    if out.get("f") == "2" and not (grounded and out.get("t") in MATERIAL):
        result["f"] = "1"
    return result


def reason(out: Mapping, line: str, *, reused: bool) -> str:
    """'hold (strong, grounded): v2 f0 t=none | <codes> | words' -- the prefix is
    what scanner.llm.scorecard parses."""
    head = (f"{_CALL[out['c']]} ({_CONF[out['k']]}, "
            f"{'grounded' if out['grounded'] else 'ungrounded'})")
    return (f"{head}: v2 f{out['f']} t={out.get('t', 'none')}{' reused' if reused else ''} "
            f"| {line} | {str(out.get('w', ''))[:80]}")[:300]
