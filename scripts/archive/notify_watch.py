"""Push what matters to the operator's phone (ntfy.sh). Never trades.

    python scripts/notify_watch.py           # send anything new since the last run
    python scripts/notify_watch.py --test    # send one test alert

Follows two records the trading path already keeps -- the audit log and the
broker's fill history -- plus the kill-switch file, and pushes only what a
person must know: fills, closed trades, stops taken or re-armed by protect.py,
unprotected positions, a failed weekend flatten, a failing news gate, an
account risk breach (risk_monitor.py), the kill switch appearing. Routine
records stay off the phone. ops/watch.py decides and words each alert.

The trading scripts are not touched: this reads what they write. The first
run only marks where "now" is, so years of history are not pushed at once.
State: data/ops/notify_state.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.safety import kill_switch_active
from scanner.ops import notify, watch

STATE = Path("data/ops/notify_state.json")


def audit_rows(start: int) -> tuple[list[dict], int]:
    path = Path(settings.audit_log_path)
    if not path.exists():
        return [], 0
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[start:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, len(lines)


async def fills_after(client, last_id: str) -> tuple[list[dict], str]:
    broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
    recent = await broker._get("/v2/account/activities/FILL", page_size=100, direction="desc")
    newest = recent[0]["id"] if recent else last_id
    new = [a for a in reversed(recent or []) if not last_id or a["id"] > last_id]
    return new, newest


async def push(client, alert: watch.Alert) -> None:
    sent = await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                             title=alert.title, message=alert.message,
                             priority=alert.priority, tags=alert.tags)
    print(f"  {'sent' if sent else 'not sent'}: [{alert.priority}] {alert.title} -- {alert.message[:90]}")


async def main(test: bool) -> int:
    async with httpx.AsyncClient(timeout=30) as client:
        if test:
            await push(client, watch.Alert(title="Quant-desk test",
                                           message="Alerts reach this phone.", tags=("white_check_mark",)))
            return 0
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
        rows, line_count = audit_rows(state["audit_lines"] if state else 0)
        try:
            fills, newest = await fills_after(client, state.get("fill_id", "") if state else "")
        except BrokerError as exc:
            print(f"  ! broker unreadable, fills skipped this run: {exc}")
            fills, newest = [], (state or {}).get("fill_id", "")
        kill = kill_switch_active(settings.kill_switch_path)
        if state is None:
            print(f"first run: marking now (audit line {line_count}, fill {newest or '-'}); nothing sent")
        else:
            alerts = watch.from_audit(rows) + watch.from_fills(fills)
            if kill and not state.get("kill_notified"):
                alerts.append(watch.Alert(title="KILL SWITCH ON", priority="urgent",
                                          message=f"{settings.kill_switch_path} exists: no new entries.",
                                          tags=("octagonal_sign",)))
            for alert in alerts:
                await push(client, alert)
            if not alerts:
                print("nothing new")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"audit_lines": line_count, "fill_id": newest,
                                 "kill_notified": kill}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--test", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args().test)))
