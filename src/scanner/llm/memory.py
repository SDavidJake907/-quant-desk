"""What this system has learned from its own trades, as a prompt block.

GROUNDED IN OUTCOMES, NOT IN TEXT
Two retrieval-style memories were tested on 2026-09-24 and both failed. Mem0
stored headlines flat and returned them by similarity; asked "is Pfizer
acquiring Seagen", it ranked the ANNOUNCEMENT above the TERMINATION, 0.778 to
0.675. Handed that context, qwen2.5:14b went from answering correctly to
answering wrongly. The memory talked a right answer into a wrong one.

The lesson is not that memory is useless. It is that memory built from TEXT
inherits whatever the text says, including things that have stopped being
true. This is built from REALIZED R instead — a stop that was hit is not a
claim that can go stale.

IT REFUSES TO GENERALISE FROM ONE TRADE
There are ten completed trades across ten distinct symbols. Per-symbol history
is n=1, and a "track record" computed from one observation is noise wearing a
number. So statistics are gated behind `MIN_SAMPLE` and the block says
`insufficient history` rather than inventing a figure — the same discipline
`what_works.py` prints UNDECIDABLE for.

What DOES work at n=1 is a rule. `cooldown` is not a prediction; it is a
refusal to re-enter a name the same day its stop was hit, which needs no
sample to justify.

NOT WIRED TO THE TRADING PATH
Nothing under the frozen file list imports this. It exists so the capability
can be built and tested before anyone decides whether the model should see it,
and that decision needs evidence this project does not yet have: the model's
own ranking has never once been scored.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from scanner.models import Frozen

#: Completed trades for one symbol before its record is worth reporting.
#: Five is not a real threshold either; it is the point below which a mean is
#: obviously meaningless rather than subtly so.
MIN_SAMPLE = 5

#: Hours after a stop-out before the same name is offered again. A rule, not
#: a measured value: re-entering a name that just took you out, on the same
#: structure that just failed, is the trade the log shows being refused 89
#: times as "already holding" and never examined.
COOLDOWN_HOURS = 24.0


class SymbolRecord(Frozen):
    """One symbol's realized history. Only built when there is enough of it."""

    symbol: str
    trades: int
    mean_r: float
    wins: int

    def describe(self) -> str:
        return (f"{self.symbol}: {self.trades} trades, "
                f"mean {self.mean_r:+.2f}R, {self.wins} won")


def _rows(path: str | Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def outcomes(path: str | Path, policy: str | None = None) -> list[dict]:
    """Completed trades, optionally narrowed to one policy fingerprint."""
    found = []
    for row in _rows(path):
        if row.get("event") != "trade_outcome":
            continue
        if policy and row.get("policy") != policy:
            continue
        outcome = row.get("outcome") or {}
        if outcome.get("symbol"):
            found.append({"at": str(row.get("at", "")), **outcome})
    return found


def records(path: str | Path, min_sample: int = MIN_SAMPLE
            ) -> tuple[SymbolRecord, ...]:
    """Per-symbol history, for symbols that have enough of it.

    Returns empty when nothing clears the bar, which is the honest answer
    today and will stop being the answer on its own.
    """
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for outcome in outcomes(path):
        by_symbol[outcome["symbol"]].append(float(outcome.get("realized_r", 0)))
    built = [
        SymbolRecord(
            symbol=symbol,
            trades=len(values),
            mean_r=statistics.fmean(values),
            wins=sum(1 for v in values if v > 0),
        )
        for symbol, values in by_symbol.items()
        if len(values) >= min_sample
    ]
    return tuple(sorted(built, key=lambda r: -r.mean_r))


def cooldown_until(path: str | Path, symbol: str,
                   hours: float = COOLDOWN_HOURS) -> datetime | None:
    """When this symbol becomes eligible again, or None if it already is.

    Only a LOSS starts a cooldown. A target that paid out is not a reason to
    avoid a name, and treating it as one would quietly filter out whatever
    was working.
    """
    latest: datetime | None = None
    for outcome in outcomes(path):
        if outcome["symbol"].upper() != symbol.upper():
            continue
        if float(outcome.get("realized_r", 0)) > 0:
            continue
        try:
            when = datetime.fromisoformat(outcome["at"])
        except ValueError:
            continue
        if latest is None or when > latest:
            latest = when
    if latest is None:
        return None
    return latest + timedelta(hours=hours)


def in_cooldown(path: str | Path, symbol: str, *,
                now: datetime | None = None,
                hours: float = COOLDOWN_HOURS) -> bool:
    until = cooldown_until(path, symbol, hours)
    if until is None:
        return False
    moment = now or datetime.now().astimezone()
    if until.tzinfo is None:
        until = until.replace(tzinfo=moment.tzinfo)
    return moment < until


#: The rolling window for "what just happened". Matches COOLDOWN_HOURS on
#: purpose: a name stopped out yesterday and a name refused yesterday are the
#: same kind of fact, and two different horizons for them would invite the
#: model to treat one as fresher than the other.
RECENT_HOURS = 24.0


def last_24h(path: str | Path, hours: float = RECENT_HOURS,
             now: datetime | None = None, model: str | None = None) -> str:
    """Everything the system did in the last `hours`, as prose.

    WHY THIS IS SEPARATE FROM THE TRADE RECORD
    `build_block` answers "what has this system learned", which needs a sample
    and refuses to speak below one. This answers "what just happened", which
    needs no sample at all -- a refusal is a fact about yesterday whether or
    not it is a fact about the strategy.

    Keeping them apart matters. Ten trades cannot support a per-symbol claim,
    and a block that mixed last night's four refusals into that record would
    read like evidence and act like noise.

    COUNTS AND REASONS, NOT VERDICTS. It reports what the funnel did and what
    the model said, without judging either. The judging is the model's job and
    it should be doing it on the facts rather than on a summary's opinion.

    ONE MODEL'S WORDS ONLY. With `model`, review texts written by any other
    model are left out and counted. Rows from before the `model` field
    existed (2026-10-04) were gemma3:12b. Facts -- fills, stops, refusals --
    are shown whoever was configured, because they do not depend on it.
    """
    moment = now or datetime.now().astimezone()
    cutoff = moment - timedelta(hours=hours)
    fresh = []
    for row in _rows(path):
        stamp = row.get("at") or ""
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        if when >= cutoff:
            fresh.append((when, row))
    if not fresh:
        return (f"RECENT ({hours:.0f}h): nothing recorded. Either the system "
                f"did not run or it found nothing.")

    by_event: dict[str, int] = defaultdict(int)
    refusals: dict[str, int] = defaultdict(int)
    opened: list[str] = []
    closed: list[str] = []
    reviews: list[str] = []
    flattens: list[str] = []
    foreign = 0
    for when, row in fresh:
        event = str(row.get("event", ""))
        by_event[event] += 1
        symbol = str(row.get("symbol", "") or "")
        if event == "candidate_refused" and row.get("reason"):
            refusals[str(row["reason"])[:60]] += 1
        elif event == "order_submitted" and symbol:
            opened.append(symbol)
        elif event == "trade_outcome":
            out = row.get("outcome") or {}
            closed.append(f"{out.get('symbol', symbol)} "
                          f"{float(out.get('realized_r', 0)):+.2f}R "
                          f"({out.get('exit_reason', '?')})")
        elif event == "weekend_flatten" and row.get("reason"):
            flattens.append(str(row["reason"])[:120])
        elif event == "position_review" and symbol:
            if model is not None and row.get("model", "") != model:
                foreign += 1
                continue
            reviews.append(f"{symbol}: {str(row.get('reason', ''))[:70]}")

    lines = [f"RECENT ({hours:.0f}h) — what this system actually did."]
    slates = by_event.get("candidate_slate", 0)
    lines.append(f"  {slates} scan(s) produced a ranked slate; "
                 f"{by_event.get('trade_intent', 0)} intent(s), "
                 f"{by_event.get('order_submitted', 0)} order(s) sent.")
    if opened:
        lines.append(f"  opened: {', '.join(sorted(set(opened)))}.")
    if closed:
        lines.append(f"  closed: {'; '.join(closed)}.")
    if flattens:
        lines.append(f"  flatten (closes sent outside the scan): {flattens[-1]}")
    if refusals:
        lines.append("  refused, by reason:")
        for reason, count in sorted(refusals.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {count}x  {reason}")
    if reviews:
        lines.append("  position reviews recorded (advisory, not acted on):")
        lines += [f"    {r}" for r in reviews[-6:]]
    if foreign:
        lines.append(f"  {foreign} review(s) by an earlier model left out.")
    if by_event.get("news_gate_degraded"):
        lines.append(f"  WARNING: the news gate failed open "
                     f"{by_event['news_gate_degraded']}x -- names passed the "
                     f"news check unrefused.")
    if by_event.get("protection_restored"):
        lines.append(f"  WARNING: {by_event['protection_restored']} position(s) "
                     f"were found unprotected and re-armed.")
    return '\n'.join(lines)


def build_block(path: str | Path, min_sample: int = MIN_SAMPLE) -> str:
    """The memory as prose the model can be shown.

    Says what is known, says what is not known, and never fills the gap. A
    block that reads confidently on ten trades would be worse than no block,
    because the model has no way to discount it.
    """
    history = outcomes(path)
    if not history:
        return "MEMORY: no completed trades yet. Nothing has been learned."

    values = [float(o.get("realized_r", 0)) for o in history]
    symbols = {o["symbol"] for o in history}
    lines = [
        "MEMORY — what this system has learned from its own completed trades.",
        f"  {len(values)} trades across {len(symbols)} symbols.",
    ]
    if len(values) > 1:
        mean = statistics.fmean(values)
        se = statistics.stdev(values) / len(values) ** 0.5
        lines.append(f"  overall mean {mean:+.2f}R, standard error {se:.2f}.")
        if mean - 1.96 * se < 0 < mean + 1.96 * se:
            lines.append("  the interval contains zero: this record cannot "
                         "yet tell profit from chance.")

    known = records(path, min_sample)
    if known:
        lines.append(f"  symbols with at least {min_sample} trades:")
        lines += [f"    {r.describe()}" for r in known]
    else:
        lines.append(f"  no symbol has {min_sample} trades yet, so there is "
                     f"no per-symbol history to report. Do not infer one.")

    cooling = sorted(s for s in symbols if in_cooldown(path, s))
    if cooling:
        lines.append(f"  recently stopped out, treat with caution: "
                     f"{', '.join(cooling)}.")
    return "\n".join(lines)
