"""Does the news BEFORE a sweep predict how it resolves? The model labels, the math decides.

    python scripts/news_event_test.py            # label (resumable) then test
    python scripts/news_event_test.py --report   # test on the cached labels only

READ-ONLY research. Changes nothing live. Uses the GPU (gemma4:12b via Ollama).

WHY THIS, AND WHY THE MODEL
Every model failure on 2026-10-04 was a judgement on numbers or charts; every
success was reading text (WORKLIST §27). Published work finds LLM-scored
headlines predict next-day returns, most strongly after BAD news, consistent
with the market under-reacting to it (Lopez-Lira & Tang). So the model does
what it is measured good at -- turn each headline into structured facts -- and
the backtest decides whether those facts matter.

DESIGN, FIXED BEFORE ANY OUTCOME WAS LOOKED AT
    setups     the gated Mon-Thu setups in the 2y Alpaca store, Friday flatten
    news       headlines tagged with the symbol in data/news_history, created in
               the 3 days before the trigger bar (point-in-time), newest 8
    labels     per headline: event type, direction, material yes/no (cached in
               data/news_labels.jsonl by headline id, symbol and model)
    bands      negative   >= 1 material negative headline
               positive   >= 1 material positive, no material negative
               quiet      no material headline (including no news at all)
    test       negative vs quiet: train picks nothing, the hypothesis is
               stated -- negative news before a sweep resolves WORSE; holdout
               gap with a permutation p. Event types reported descriptively.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.llm.ollama import LLMError, chat_json

from relative_strength_test import permutation_p
from sizing_research import _work

NEWS = Path("data/news_history")
LABELS = Path("data/news_labels.jsonl")
DAYS, PER_SETUP, BATCH = 3, 8, 10
EVENTS = ["earnings_beat", "earnings_miss", "guidance_up", "guidance_down",
          "analyst_upgrade", "analyst_downgrade", "mna", "legal_regulatory",
          "product", "management", "macro_sector", "other"]

SCHEMA = {"type": "object", "properties": {"labels": {"type": "array", "items": {
    "type": "object", "properties": {
        "index": {"type": "integer"},
        "event": {"type": "string", "enum": EVENTS},
        "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "material": {"type": "boolean"}},
    "required": ["index", "event", "direction", "material"]}}},
    "required": ["labels"]}

PROMPT = """Label each headline about {company} ({ticker}) for its likely effect on
{ticker}'s stock price. Judge only what the headline says; do not guess beyond it.

event: one of {events}
  macro_sector = about the market or the industry rather than this company
  other = none of the above, or not really about this company
direction: positive, negative or neutral for {ticker}'s price
material: true only if a reasonable investor would trade on it -- an earnings
  result, guidance change, rating change, deal, lawsuit or regulatory action,
  major product event. Generic articles, listicles and price recaps are NOT.

Return one label per headline, using its index.

{items}
"""


def company_names() -> dict[str, str]:
    with open("data/constituents.csv", encoding="utf-8") as handle:
        return {r["Symbol"]: r.get("Security", r["Symbol"]) for r in csv.DictReader(handle)}


def headlines(symbol: str, moment: datetime) -> list[tuple[str, str]]:
    """(id, headline) tagged with symbol, created in the DAYS before moment, newest first."""
    found = {}
    for back in range(DAYS + 1):
        path = NEWS / f"{(moment - timedelta(days=back)).date().isoformat()}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
                when = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue
            if symbol in (row.get("symbols") or []) and moment - timedelta(days=DAYS) <= when < moment:
                found[str(row["id"])] = (when, row.get("headline", ""))
    ranked = sorted(found.items(), key=lambda kv: kv[1][0], reverse=True)
    return [(i, h) for i, (_, h) in ranked if h][:PER_SETUP]


def load_cache(model: str) -> dict[tuple[str, str], dict]:
    out = {}
    if LABELS.exists():
        for line in LABELS.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("model") == model:
                out[(row["id"], row["symbol"])] = row
    return out


async def label(todo: dict[str, list[tuple[str, str]]], model: str) -> None:
    names = company_names()
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(v) for v in todo.values())
    done = 0
    async with httpx.AsyncClient(timeout=600) as client:
        for symbol, items in todo.items():
            for start in range(0, len(items), BATCH):
                batch = items[start:start + BATCH]
                prompt = PROMPT.format(company=names.get(symbol, symbol), ticker=symbol,
                                       events=", ".join(EVENTS),
                                       items="\n".join(f"[{k}] {h}" for k, (_, h) in enumerate(batch)))
                try:
                    out = await chat_json(prompt, SCHEMA, client, model=model)
                except LLMError as exc:
                    print(f"  ! {symbol}: {exc}")
                    continue
                with LABELS.open("a", encoding="utf-8") as handle:
                    for lab in out.get("labels", []):
                        k = lab.get("index")
                        if isinstance(k, int) and 0 <= k < len(batch):
                            handle.write(json.dumps({"id": batch[k][0], "symbol": symbol, "model": model,
                                                     "event": lab["event"], "direction": lab["direction"],
                                                     "material": bool(lab["material"])}) + "\n")
                done += len(batch)
                if done % 200 < len(batch):
                    print(f"  {done}/{total} headlines labelled", flush=True)


def band(labels: list[dict]) -> str:
    material = [x for x in labels if x["material"]]
    if any(x["direction"] == "negative" for x in material):
        return "negative"
    if any(x["direction"] == "positive" for x in material):
        return "positive"
    return "quiet"


def stats(vals):
    if not vals:
        return "n=0"
    m = statistics.fmean(vals)
    return f"{m:+.3f}R n={len(vals):<4} win {sum(v > 0 for v in vals) / len(vals) * 100:.0f}%"


def report(setups, model: str) -> None:
    cache = load_cache(model)
    rows = []
    for s in setups:
        labs = [cache[(i, s["symbol"])] for i, _ in s["news"] if (i, s["symbol"]) in cache]
        unlabelled = len(s["news"]) - len(labs)
        rows.append((s["at"], band(labs), s["r"], labs, unlabelled))
    cut = len(rows) * 2 // 3
    parts = {"train": rows[:cut], "holdout": rows[cut:]}
    with_news = sum(1 for s in setups if s["news"])
    missing = sum(r[4] for r in rows)
    print(f"\n{len(rows)} gated setups, {with_news} with headlines in the {DAYS} days before; "
          f"{missing} headline(s) unlabelled; train to {rows[cut - 1][0]:%Y-%m-%d}")
    for name in ("negative", "positive", "quiet"):
        print(f"  {name:<9} train {stats([r[2] for r in parts['train'] if r[1] == name]):<30}"
              f"holdout {stats([r[2] for r in parts['holdout'] if r[1] == name])}")
    for part in ("train", "holdout"):
        neg = [r[2] for r in parts[part] if r[1] == "negative"]
        quiet = [r[2] for r in parts[part] if r[1] == "quiet"]
        if len(neg) >= 10 and len(quiet) >= 10:
            gap = statistics.fmean(neg) - statistics.fmean(quiet)
            p = permutation_p(neg, quiet, 20_000)
            print(f"  negative minus quiet, {part}: {gap:+.3f}R  p={p:.3f}")
        else:
            print(f"  negative minus quiet, {part}: too few ({len(neg)} vs {len(quiet)})")
    events = defaultdict(list)
    for _, _, r, labs, _ in rows:
        for e in {x["event"] for x in labs if x["material"]}:
            events[e].append(r)
    print("\n  setups with a MATERIAL headline of each type (descriptive, all periods):")
    for e, vals in sorted(events.items(), key=lambda kv: -len(kv[1])):
        print(f"    {e:<18}{stats(vals)}")


async def main(report_only: bool, model: str) -> int:
    symbols = sorted({f.name.split("__")[0] for f in Path("data/intraday_alpaca").glob("*.json")})
    with ProcessPoolExecutor() as pool:
        raw = [r for chunk in pool.map(_work, [(s, None, None) for s in symbols], chunksize=8)
               for r in chunk]
    raw.sort(key=lambda r: r["at"])
    setups = []
    for r in raw:
        moment = r["at"] if r["at"].tzinfo else r["at"].replace(tzinfo=timezone.utc)
        setups.append({"at": r["at"], "symbol": r["symbol"], "r": r["r"],
                       "news": headlines(r["symbol"], moment)})
    if not report_only:
        cache = load_cache(model)
        todo: dict[str, list] = defaultdict(list)
        seen = set()
        for s in setups:
            for i, h in s["news"]:
                key = (i, s["symbol"])
                if key not in cache and key not in seen:
                    seen.add(key)
                    todo[s["symbol"]].append((i, h))
        print(f"{sum(len(v) for v in todo.values())} headline(s) to label with {model}")
        await label(dict(todo), model)
    report(setups, model)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", action="store_true")
    p.add_argument("--model", default=settings.triage_model)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.report, a.model)))
