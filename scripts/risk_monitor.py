"""Account-level hard stops: 3% in a day, 6% in a week, 15% below the high.

    python scripts/risk_monitor.py             # check; record a breach (record-only)
    python scripts/risk_monitor.py --enforce   # ALSO create the kill switch on a breach

Operator's limits, 2026-10-06 (GO_LIVE.md). A breach means stop and review:
no new entries until a human has written down what happened. Until funding
this runs RECORD-ONLY -- it logs `risk_breach` (which notify_watch.py pushes to
the phone) and says it would have engaged the kill switch, but does not. With
--enforce it creates the kill-switch file trade.py already honours; deleting
that file is the deliberate human step back.

READ-ONLY against the broker: account and daily equity history. One alert per
limit per day.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.features.intraday import EXCHANGE_TZ
from scanner.models import AuditEvent
from scanner.ops import risk

STATE = Path("data/ops/risk_state.json")
LIMITS = risk.Limits(day_pct=3.0, week_pct=6.0, drawdown_pct=15.0)


async def read_broker():
    async with httpx.AsyncClient(timeout=30) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker._get("/v2/account")
        history = await broker._get("/v2/account/portfolio/history", period="1A", timeframe="1D")
    return account, history


def main(enforce: bool) -> int:
    try:
        account, history = asyncio.run(read_broker())
    except BrokerError as exc:
        print(f"risk monitor: broker unreadable: {exc}")
        return 1
    equity = float(account.get("equity") or 0)
    day_start = float(account.get("last_equity") or equity)
    pairs = [(datetime.fromtimestamp(ts, tz=timezone.utc).date(), float(eq))
             for ts, eq in zip(history.get("timestamp") or [], history.get("equity") or []) if eq]
    today = datetime.now(tz=EXCHANGE_TZ).date()
    week = risk.week_start(pairs, today=today) if pairs else day_start
    peak = max([eq for _, eq in pairs] + [equity])
    breaches = risk.evaluate(equity=equity, day_start=day_start, week_start=week, peak=peak, limits=LIMITS)
    print(f"risk: equity {equity:,.2f} | day {risk._loss(day_start, equity):+.2f}% of {LIMITS.day_pct}% "
          f"| week {risk._loss(week, equity):+.2f}% of {LIMITS.week_pct}% "
          f"| from high {risk._loss(peak, equity):+.2f}% of {LIMITS.drawdown_pct}%  (losses positive)")
    if not breaches:
        return 0

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    seen = set(state.get(today.isoformat(), []))
    for b in breaches:
        if b.name in seen:
            continue
        action = "kill switch ENGAGED" if enforce else "record-only: kill switch NOT engaged (enforce at funding)"
        why = f"{b.name} loss {b.loss_pct:.2f}% >= limit {b.limit_pct:.1f}% (equity {equity:,.2f}); {action}"
        append_event(settings.audit_log_path, AuditEvent(
            event="risk_breach", at=datetime.now().astimezone(), symbol="", stage="risk", reason=why))
        print(f"  BREACH {why}")
        if enforce:
            Path(settings.kill_switch_path).write_text(
                f"Engaged {datetime.now().astimezone():%Y-%m-%d %H:%M %Z} by risk_monitor.py: {why}\n"
                "Write down what happened, then delete this file to resume.\n", encoding="utf-8")
        seen.add(b.name)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({today.isoformat(): sorted(seen)}), encoding="utf-8")
    return 2


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--enforce", action="store_true")
    raise SystemExit(main(p.parse_args().enforce))
