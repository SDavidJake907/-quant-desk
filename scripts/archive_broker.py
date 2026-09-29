"""Copy everything the broker holds, before the broker stops holding it.

    python scripts/archive_broker.py

READ-ONLY against Alpaca. Writes a dated snapshot to data/broker_archive.

WHY THIS EXISTS
The audit log records what this system DECIDED. The broker records what
actually FILLED. Those are different halves of the same evidence and only the
first is in git.

`reconcile.py` already names the exposure: "realized R is otherwise recomputed
from Alpaca's order history, which the broker prunes." So the fills -- the only
out-of-sample evidence this project has -- live somewhere nobody here controls.

Twelve closed trades is a thin record. Losing it would not dent the strategy; it
would remove the only thing that is not a backtest.

WHAT WOULD TAKE IT
Routine pruning, an account reset, a key rotation, or a clearing migration. A
change of clearing firm in particular is the kind of event that resets paper
accounts and truncates order history, and it is the class of change no
identifier in FREEZE.md captures -- the policy fingerprint, the build SHA and
the model environment all stay put while the ground moves, exactly as with the
23x5 market change on 2026-12-06.

WHAT IS TAKEN
  orders             every order ever placed, nested so bracket legs come too
  account            equity, buying power, status at the moment of the snapshot
  portfolio_history  the daily equity curve, which cannot be rebuilt from orders
  activities         fills, dividends, fees -- whatever the endpoint offers

Dated filenames, never overwritten. An archive that can be clobbered by a later
run is not an archive.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

OUT = Path("data/broker_archive")


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


async def main(paper: bool) -> int:
    if not settings.alpaca_key_id:
        print(f"{RED}Alpaca keys are not configured{RESET}")
        return 2
    host = ("https://paper-api.alpaca.markets/v2" if paper
            else "https://api.alpaca.markets/v2")
    OUT.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=90) as client:
        # Paginated by submitted_at: the endpoint caps a single page, and a
        # truncated archive is worse than none because it looks complete.
        orders: list = []
        after = None
        while True:
            params = {"status": "all", "limit": 500, "direction": "asc",
                      "nested": "true"}
            if after:
                params["after"] = after
            response = await client.get(f"{host}/orders", headers=_headers(),
                                        params=params)
            if response.status_code != 200:
                print(f"{YELLOW}orders: HTTP {response.status_code}{RESET}")
                break
            page = response.json()
            if not page:
                break
            orders += page
            if len(page) < 500:
                break
            after = page[-1]["submitted_at"]

        async def get(path: str, **params):
            r = await client.get(f"{host}{path}", headers=_headers(),
                                 params=params)
            return r.json() if r.status_code == 200 else None

        account = await get("/account")
        history = await get("/account/portfolio/history", period="12M",
                            timeframe="1D")
        activities = await get("/account/activities", page_size=500)

    stamp = datetime.now().strftime("%Y-%m-%d")
    written = []
    for name, payload in (("orders", orders), ("account", account),
                          ("portfolio_history", history),
                          ("activities", activities)):
        if payload is None:
            continue
        path = OUT / f"{stamp}__{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        written.append(path)

    fills = [o for o in orders if o.get("filled_at")]
    print(f"  {'orders':<18}{len(orders):>6}  ({len(fills)} filled)")
    if history:
        print(f"  {'equity points':<18}{len(history.get('equity', [])):>6}")
    if activities is not None:
        print(f"  {'activities':<18}{len(activities):>6}")
    if fills:
        print(f"  {DIM}fills span {fills[0]['filled_at'][:10]} -> "
              f"{fills[-1]['filled_at'][:10]}{RESET}")
    print(f"\n  {GREEN}{len(written)}{RESET} file(s) -> {OUT}/{stamp}__*.json")
    print(f"  {DIM}Dated, never overwritten. Commit them: the broker's copy is "
          f"the one that can disappear.{RESET}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="archive the LIVE account instead of paper")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(paper=not args.live)))
