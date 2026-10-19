"""stock-scanner dashboard -- a read-only view of the competition.

Shows the month's scoreboard (you vs Gemma, ghosts, S&P, momentum), each
book's open positions, recent trading activity and whether the scheduled jobs
are running. It only reads: there is no endpoint that edits a watchlist, runs
a script or touches the broker beyond GET requests. Lists are still edited by
hand and committed, as FREEZE.md requires.

Run (binds to this PC only):
    .venv\\Scripts\\python.exe -m uvicorn scanner.web.app:app --port 8000
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from scanner.config import settings
from scanner.execution.alpaca import PAPER_HOST, Alpaca, BrokerError
from scanner.execution.safety import kill_switch_active
from scanner.web import readers

ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
COMPETITION = LOGS / "competition"
WATCHLISTS = ROOT / "watchlists"

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


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("scanner.web.app:app", host="127.0.0.1", port=8000)
