"""stock-scanner dashboard -- the competition, and Gemma's review.

Shows the month's scoreboard (you vs Gemma, ghosts, S&P, momentum), each
book's open positions, recent trading activity, whether the scheduled jobs
are running, and Gemma's evening review (scripts/gemma_review.py).

ONE write endpoint (operator 2026-10-19): POST /api/ask records a question for
Gemma and answers it in the background. It trades nothing and changes no rule.
It accepts JSON only, from a page on this PC only (Origin check), so another
website open in the browser cannot post to it. Nothing here edits a watchlist,
runs a script or sends anything to the broker but GET requests.

Run (binds to this PC only):
    .venv\\Scripts\\python.exe -m uvicorn scanner.web.app:app --port 8000
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from scanner.config import settings
from scanner.execution.alpaca import PAPER_HOST, Alpaca, BrokerError
from scanner.execution.safety import kill_switch_active
from scanner.advisor import gemma_review as rv
from scanner.web import asks, readers

ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
COMPETITION = LOGS / "competition"
WATCHLISTS = ROOT / "watchlists"
GEMMA_DATA = ROOT / "data" / "gemma"
REVIEWS = GEMMA_DATA / "reviews"
ASKS = GEMMA_DATA / "asks.jsonl"
LOCAL_HOSTS = ("127.0.0.1", "localhost")

app = FastAPI(title="stock-scanner", docs_url=None, redoc_url=None, openapi_url=None)
# Refuse requests whose Host is not this PC (blocks DNS-rebinding pages from
# reading the account through the browser).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/scoreboard")
async def scoreboard() -> dict[str, Any]:
    standings = COMPETITION / "standings.md"
    return {
        **readers.parse_standings(readers.read_text(standings)),
        "selection": readers.parse_selection(readers.read_text(COMPETITION / "selection.md")),
        "pl_series": readers.pl_series(ROOT / "data" / "competition" / "snapshots.jsonl"),
        "updated": readers.modified_at(standings),
    }


@app.get("/api/books")
async def books() -> dict[str, Any]:
    """Account and positions by book, from the paper broker (GET only)."""
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        return {"ok": False, "error": "Alpaca keys are not configured in .env"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
            account, positions, all_orders, open_orders, clock = await asyncio.gather(
                broker.account(),
                broker._get("/v2/positions"),
                broker._get("/v2/orders", status="all", limit=500, nested="true"),
                broker._get("/v2/orders", status="open", nested="true"),
                broker._get("/v2/clock"),
            )
    except (BrokerError, httpx.HTTPError) as exc:
        return {"ok": False, "error": f"broker unreachable: {exc}"}
    return {
        "ok": True,
        "paper": broker.host == PAPER_HOST,
        "account": {"equity": account.equity, "cash": account.cash,
                    "buying_power": account.buying_power, "status": account.status},
        "market_open": bool(clock.get("is_open")),
        "books": readers.group_positions(positions, all_orders, open_orders),
        "lists": {name: readers.watchlist_symbols(WATCHLISTS / f"{name}.txt")
                  for name in ("picks", "gemma", "core")},
    }


@app.get("/api/system")
async def system() -> dict[str, Any]:
    return {
        "freeze": readers.freeze_header(readers.read_text(ROOT / "FREEZE.md")),
        "kill_switch": kill_switch_active(ROOT / settings.kill_switch_path),
        "jobs": readers.schedule_health(LOGS),
        "activity": readers.activity(ROOT / settings.audit_log_path),
        "server_time": datetime.now().astimezone().isoformat(),
    }


@app.get("/api/review")
async def review() -> dict[str, Any]:
    """Gemma's latest evening review, and which days have one."""
    days = sorted((p.stem for p in REVIEWS.glob("????-??-??.json")), reverse=True)
    return {"review": asks.latest_review(REVIEWS), "days": days[:30]}


@app.get("/api/asks")
async def ask_history() -> dict[str, Any]:
    rows = asks.load(ASKS)
    return {"asks": rows[:50], "pending": asks.pending(rows)}


def _from_this_pc(request: Request) -> bool:
    """A same-origin page sends Origin http://127.0.0.1:<port> or localhost."""
    origin = request.headers.get("origin", "")
    host = origin.split("://", 1)[-1].split(":", 1)[0]
    return origin.startswith("http://") and host in LOCAL_HOSTS


@app.post("/api/ask")
async def ask(request: Request) -> dict[str, Any]:
    if not _from_this_pc(request):
        raise HTTPException(403, "questions are accepted only from the dashboard on this PC")
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "send JSON")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "send JSON") from None
    question = rv.clean_question(body.get("question") if isinstance(body, dict) else None)
    if question is None:
        raise HTTPException(400, f"ask a question of 1-{rv.MAX_QUESTION_CHARS} characters")
    if asks.pending(asks.load(ASKS)):
        raise HTTPException(429, "Gemma is still answering the last question")
    ask_id = asks.submit(ASKS, question, questions_dir=GEMMA_DATA / "questions",
                         reviews_dir=REVIEWS)
    return {"ok": True, "id": ask_id}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("scanner.web.app:app", host="127.0.0.1", port=8000)
