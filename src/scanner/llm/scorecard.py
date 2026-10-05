"""Mark the model's live calls against what the trades then did.

THREE THINGS THE MODEL DECIDES, ONE OF WHICH IS ENFORCED
  entry conviction   strong / neutral / weak on each candidate   recorded only
  position review    hold / exit, with a confidence, every scan  recorded only
  news gate          refuses takeover targets, structural news   ENFORCED

None of them had been scored. Every review in the first two months said
"hold", and nothing here could say whether "strong" carried information or
whether the gate's refusals cost or saved anything.

HOW CALLS ARE JOINED TO TRADES
The client order id is `smc-{SYMBOL}-{epoch}`, the epoch being the trigger
bar. A slate entry carries the same bar as `trigger_at`, so an entry
conviction joins its trade exactly. A review joins the latest trade in that
symbol whose trigger bar precedes it and whose outcome was recorded after it;
a review with no such trade is of a position still open, and is pending.

WHAT THIS DOES NOT DO
It does not price the gate's refusals -- that needs the bars after each one
and a counterfactual entry, which is a separate measurement. It lists them.
And it reports group sizes beside every mean, because at this sample size the
sizes are the finding.

PURE. Reads rows, returns a Scorecard. Nothing here touches the broker.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import datetime

from scanner.models import Frozen

#: Below this, a group's mean is printed but flagged as unreadable.
MIN_GROUP = 10

#: A TradingAgents rating older than this at entry is not counted as having
#: informed it: the pre-market pass rates the previous session's slate.
TA_LOOKBACK_S = 3 * 86_400

#: Refusal reasons that come from the model, not the calendar or sizing.
MODEL_REFUSALS = ("being acquired", "structural news")

_REVIEW = re.compile(r"^\s*(\w+)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")
_ORDER_ID = re.compile(r"^smc-[A-Z.\-]+-(\d{9,})$")


class ReviewedTrade(Frozen):
    symbol: str
    realized_r: float
    exit_reason: str
    reviews: int
    exits_called: int
    last_confidence: str


class Group(Frozen):
    label: str
    n: int
    mean_r: float
    wins: int

    @property
    def readable(self) -> bool:
        return self.n >= MIN_GROUP


class Scorecard(Frozen):
    reviews_total: int = 0
    reviews_pending: int = 0
    exit_calls: int = 0
    reviewed_trades: tuple[ReviewedTrade, ...] = ()
    review_groups: tuple[Group, ...] = ()
    conviction_groups: tuple[Group, ...] = ()
    gate_refusals: tuple[tuple[str, str, str], ...] = ()
    gate_degraded: int = 0
    ta_ratings: int = 0
    ta_errors: int = 0
    #: Realized R by the latest TradingAgents rating in the TA_LOOKBACK_S
    #: before entry -- the one that could have filtered the trade.
    ta_entry_groups: tuple[Group, ...] = ()
    #: ...and by the latest rating made while the position was open.
    ta_during_groups: tuple[Group, ...] = ()


def entry_epoch(client_order_id: str) -> int | None:
    match = _ORDER_ID.match(client_order_id or "")
    return int(match.group(1)) if match else None


def parse_review(reason: str) -> tuple[str, str, bool] | None:
    match = _REVIEW.match(reason or "")
    if not match:
        return None
    call, confidence, grounding = (g.lower() for g in match.groups())
    return call, confidence, grounding == "grounded"


def _epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _submissions(rows) -> dict[str, dict]:
    """Entry orders by client order id: when sent, and what the model said."""
    found: dict[str, dict] = {}
    for row in rows:
        intent = row.get("intent") or {}
        coid = str(intent.get("client_order_id") or "")
        when = _epoch(str(row.get("at", "")))
        if coid and when is not None and coid not in found:
            found[coid] = {"symbol": str(row.get("symbol") or intent.get("symbol")),
                           "at": when,
                           "conviction": str(intent.get("llm_conviction") or "")}
    return found


def _trades(rows, sent: dict[str, dict]) -> list[dict]:
    """Completed trades with a parseable trigger bar, oldest first.

    A trade's window runs from its trigger bar to whichever comes first: its
    outcome being RECORDED (the log has no exit time, and recording can lag
    the exit by a day) or the next entry order in the same symbol. The second
    bound is what stops a re-entry's reviews being scored as the old trade's.
    """
    found: dict[str, dict] = {}
    for row in rows:
        out = row.get("outcome") or {}
        coid = str(out.get("client_order_id", ""))
        start = entry_epoch(coid)
        closed = _epoch(str(row.get("at", "")))
        if start is None or closed is None or not out.get("symbol") or coid in found:
            continue
        try:
            r = float(out.get("realized_r") or 0.0)
        except (TypeError, ValueError):
            continue
        own = sent.get(coid, {}).get("at", start)
        later = [s["at"] for c, s in sent.items()
                 if c != coid and s["symbol"] == out["symbol"] and s["at"] > own]
        found[coid] = {"symbol": out["symbol"], "start": start, "coid": coid,
                       "closed": min([closed, *later]), "r": r,
                       "exit": str(out.get("exit_reason", "")),
                       "policy": row.get("policy")}
    return sorted(found.values(), key=lambda t: t["start"])


def _owner(trades, symbol: str, when: float) -> dict | None:
    """The trade in `symbol` that was open at `when`, if any."""
    for trade in reversed(trades):
        if trade["symbol"] == symbol and trade["start"] <= when <= trade["closed"]:
            return trade
    return None


def _group(label: str, values: list[float]) -> Group:
    return Group(label=label, n=len(values), mean_r=statistics.fmean(values),
                 wins=sum(v > 0 for v in values))


def _score_reviews(rows, trades, policy: str | None):
    per_trade: dict[int, list[tuple[float, str, str]]] = defaultdict(list)
    total = pending = exits = 0
    for row in rows:
        parsed = parse_review(str(row.get("reason", "")))
        when = _epoch(str(row.get("at", "")))
        if parsed is None or when is None:
            continue
        call, confidence, _ = parsed
        trade = _owner(trades, str(row.get("symbol", "")), when)
        # A review belongs to its trade's policy; only an unmatched (still
        # open) review is judged by its own row.
        owner_policy = trade["policy"] if trade else row.get("policy")
        if policy is not None and owner_policy != policy:
            continue
        total += 1
        exits += call == "exit"
        if trade is None:
            pending += 1
            continue
        per_trade[id(trade)].append((when, call, confidence))

    reviewed, by_conf = [], defaultdict(list)
    for trade in trades:
        calls = sorted(per_trade.get(id(trade), []))
        if not calls:
            continue
        last = calls[-1][2]
        reviewed.append(ReviewedTrade(
            symbol=trade["symbol"], realized_r=trade["r"],
            exit_reason=trade["exit"], reviews=len(calls),
            exits_called=sum(c == "exit" for _, c, _ in calls),
            last_confidence=last))
        by_conf[last].append(trade["r"])
    groups = tuple(_group(k, v) for k, v in sorted(by_conf.items()))
    return total, pending, exits, tuple(reviewed), groups


def _score_conviction(rows, trades, sent: dict[str, dict]) -> tuple[Group, ...]:
    """Realized R grouped by the model's conviction at entry.

    Taken from the order that was sent, which is what the model said at the
    moment of entry. The slate is the fallback for trades whose order record
    predates the field; it is re-ranked every scan, so the FIRST sighting of
    the trigger bar is kept, the one nearest the entry.
    """
    by_key = {(t["symbol"], t["start"]): t for t in trades}
    from_slate: dict[tuple[str, int], str] = {}
    for row in rows:
        for item in row.get("slate") or []:
            bar = _epoch(str(item.get("trigger_at", "")))
            if bar is None:
                continue
            key = (str(item.get("symbol", "")), int(bar))
            if key in by_key:
                from_slate.setdefault(key, str(item.get("llm_conviction") or ""))
    by_label = defaultdict(list)
    for key, trade in by_key.items():
        label = sent.get(trade["coid"], {}).get("conviction") or from_slate.get(key)
        if label is None:
            continue
        by_label[label or "unranked"].append(trade["r"])
    return tuple(_group(k, v) for k, v in sorted(by_label.items()))


def _gate(rows) -> tuple[tuple[str, str, str], ...]:
    """Model refusals, one per (symbol, reason), first sighting kept."""
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        reason = str(row.get("reason", ""))
        if not reason.lower().startswith(MODEL_REFUSALS):
            continue
        key = (str(row.get("symbol", "")), reason)
        seen.setdefault(key, str(row.get("at", ""))[:10])
    return tuple((s, day, r) for (s, r), day in seen.items())


def _score_tradingagents(rows, trades, sent: dict[str, dict]):
    """Realized R by TradingAgents' rating: before entry, and while open."""
    ratings = defaultdict(list)          # symbol -> [(when, label)]
    for row in rows:
        when = _epoch(str(row.get("at", "")))
        label = str(row.get("reason", "")).split(";", 1)[0].strip()
        if when is not None and label:
            ratings[str(row.get("symbol", ""))].append((when, label))
    before, during = defaultdict(list), defaultdict(list)
    for trade in trades:
        entry = sent.get(trade["coid"], {}).get("at", trade["start"])
        mine = sorted(ratings.get(trade["symbol"], []))
        prior = [lbl for w, lbl in mine if entry - TA_LOOKBACK_S <= w <= entry]
        open_ = [lbl for w, lbl in mine if entry < w <= trade["closed"]]
        if prior:
            before[prior[-1]].append(trade["r"])
        if open_:
            during[open_[-1]].append(trade["r"])
    as_groups = lambda d: tuple(_group(k, v) for k, v in sorted(d.items()))
    return as_groups(before), as_groups(during)


def score(rows, policy: str | None = None) -> Scorecard:
    """Score every recorded model call in `rows` (audit-log dicts).

    With `policy`, a trade is selected by the fingerprint on its OUTCOME and
    its reviews follow it, whatever fingerprint they were written under.
    Joining first and filtering after is what keeps a policy change between
    entry and review from orphaning the reviews.
    """
    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_event[str(row.get("event", ""))].append(row)
    sent = _submissions(by_event["order_submitted"])
    trades = _trades(by_event["trade_outcome"], sent)
    total, pending, exits, reviewed, groups = _score_reviews(
        by_event["position_review"], trades, policy)
    mine = [t for t in trades if policy is None or t["policy"] == policy]

    def own(event: str) -> list[dict]:
        return [r for r in by_event[event]
                if policy is None or r.get("policy") == policy]

    ta_rows = own("tradingagents_rating")
    ta_before, ta_during = _score_tradingagents(ta_rows, mine, sent)

    return Scorecard(
        ta_ratings=len(ta_rows), ta_errors=len(own("tradingagents_error")),
        ta_entry_groups=ta_before, ta_during_groups=ta_during,
        reviews_total=total, reviews_pending=pending, exit_calls=exits,
        reviewed_trades=reviewed, review_groups=groups,
        conviction_groups=_score_conviction(by_event["candidate_slate"], mine, sent),
        gate_refusals=_gate(own("candidate_refused")),
        gate_degraded=len(own("news_gate_degraded")),
    )
