"""Bounded supervisor for scan-only and data-collection jobs.

Examples:
    python scripts/watchdog.py -- python scripts/collect_intraday.py --index all
    python scripts/watchdog.py --max-restarts 5 -- python scripts/trade.py --dry-run

The supervisor deliberately refuses ``--submit-paper`` and ``--live``. A
process that may have reached a broker but crashed before receiving a response
must be reconciled against the broker before it is restarted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _log(path: Path, event: str, **fields: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def run(command: list[str], *, max_restarts: int, backoff: float,
        max_backoff: float, log_path: Path) -> int:
    """Run a command, restarting only after bounded non-zero exits."""
    forbidden = {"--submit-paper", "--live"}
    if forbidden.intersection(command):
        raise ValueError("watchdog refuses to supervise order-submission commands")
    if not command:
        raise ValueError("a command is required after --")

    restarts = 0
    while True:
        _log(log_path, "started", command=command, restart=restarts)
        process = subprocess.Popen(command)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait(timeout=10)
            _log(log_path, "stopped_by_operator", command=command)
            return 130

        if code == 0:
            _log(log_path, "completed", command=command, exit_code=code)
            return 0
        _log(log_path, "crashed", command=command, exit_code=code,
             restart=restarts)
        if restarts >= max_restarts:
            _log(log_path, "restart_limit_reached", command=command,
                 max_restarts=max_restarts)
            return code
        delay = min(max_backoff, backoff * (2 ** restarts))
        time.sleep(delay)
        restarts += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--backoff", type=float, default=5.0)
    parser.add_argument("--max-backoff", type=float, default=120.0)
    parser.add_argument("--log", type=Path, default=Path("logs/watchdog.jsonl"))
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="command to supervise; put it after --")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return run(command, max_restarts=args.max_restarts,
                   backoff=args.backoff, max_backoff=args.max_backoff,
                   log_path=args.log)
    except ValueError as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
