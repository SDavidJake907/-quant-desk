"""Ask the desk's data a question; get an honest answer (rules in scanner/ops/answer.py).

    python scripts/ask.py              # every standing question
    python scripts/ask.py picks sweep  # just these

Each answer says how big, whether it is luck after the tries counted in
TRIALS.md, whether one lucky trade carries it, whether there is enough data,
and P(really above zero) under a skeptical prior. READ-ONLY.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.ownership import strategy_of
from scanner.execution.scorecard import read_rows
from scanner.ops import answer as a
from scanner.ops import competition as c
from scanner.ops import ghost_scanner as gs

#: The sweep's unseen 2018-24 test (TRIALS #1, WORKLIST §30) and R's measured
#: spread (backtest/power.py). Published numbers, not re-run here.
SWEEP_2018_24 = (-0.041, 1646)
R_SD = 1.848


def closed_r(rows, strategy: str) -> list[float]:
    """Realised R per closed trade, one per client order id."""
    seen, out = set(), []
    for r in rows:
        if r.get("event") != "trade_outcome":
            continue
        o = r.get("outcome") or {}
        coid = str(o.get("client_order_id") or "")
        if coid and coid not in seen and strategy_of(coid) == strategy:
            seen.add(coid)
            out.append(float(o.get("realized_r") or 0.0))
    return out


def ghost_r() -> list[float]:
    path = Path("data/competition/ghost_scanner.json")
    if not path.exists():
        return []
    return [t.r for t in gs.load(path.read_text(encoding="utf-8"))[0] if t.exit_price is not None]


def questions() -> dict:
    rows = read_rows(settings.audit_log_path)
    picks, gemma, sweep, ghost = (closed_r(rows, "picks"), closed_r(rows, "gemma"),
                                  closed_r(rows, "smc"), ghost_r())
    snaps_path = Path("data/competition/snapshots.jsonl")
    snaps = c.load_snapshots(snaps_path.read_text(encoding="utf-8")) if snaps_path.exists() else []
    g, p = c.daily_returns(snaps, c.GEMMA), c.daily_returns(snaps, c.PICKS)
    daily = [g[d] - p[d] for d in sorted(set(g) & set(p))]

    def maybe(values, build):
        return (lambda: build(values)) if len(values) >= 2 else None

    return {
        "picks": maybe(picks, lambda v: a.one_sample(
            v, question=f"Does the picks book make money per trade? ({len(v)} closed)",
            searched=False)),
        "gemma": maybe(gemma, lambda v: a.one_sample(
            v, question=f"Does Gemma's book make money per trade? ({len(v)} closed)",
            searched=False)),
        "sweep": maybe(sweep, lambda v: a.one_sample(
            v, question=f"Did the sweep scanner's live trades have an edge? ({len(v)} closed; "
                        "rule found by searching)")),
        "ghost": maybe(ghost, lambda v: a.one_sample(
            v, question=f"Does the ghost scanner have an edge on new data? ({len(v)} closed)",
            searched=False)),
        "sweep-all": lambda: a.pooled(
            [SWEEP_2018_24, (sum(sweep) / len(sweep), len(sweep)) if sweep else (0.0, 0),
             (sum(ghost) / len(ghost), len(ghost)) if ghost else (0.0, 0)], R_SD,
            question=f"The sweep rule on ALL its evidence (2018-24 n={SWEEP_2018_24[1]}, "
                     f"live n={len(sweep)}, ghost n={len(ghost)})", searched=False),
        "gemma-vs-you": maybe(daily, lambda v: a.one_sample(
            v, unit="% per day", question=f"Is Gemma really beating the picks book? "
                                          f"({len(v)} days, daily return difference)",
            searched=False)),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("names", nargs="*")
    wanted = p.parse_args().names
    qs = questions()
    for name, build in qs.items():
        if wanted and name not in wanted:
            continue
        if build is None:
            print(f"[{name}] not enough results yet\n")
            continue
        print(f"[{name}] {build().text()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
