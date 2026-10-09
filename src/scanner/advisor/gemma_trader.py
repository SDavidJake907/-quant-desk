"""Gemma's own book: the junior trader (WORKLIST §42).

THE CHARTER
Gemma runs a real paper book of `gemma_allocation_pct` of the account. Each
morning it chooses up to `gemma_max_positions` names from watchlists/core.txt
+ watchlists/gemma.txt (minus anything another book claims) and gives a
reason quoting its evidence. Judged 2027-04-06: its book must beat BOTH the
S&P 500 and the momentum book over the same days, worst drawdown under 25%.
If it does, it earns a wider list and a bigger sleeve; if not, it goes back
to advising. It is shown its running scoreboard every morning.

WHAT IT CONTROLS, AND WHAT IT CANNOT
It names stocks, and since 2026-10-08 a menu choice per new buy (below). The
answer schema has no numeric field and every field it chooses from is an enum,
so it cannot emit a price, a share count, or a ticker outside its list. The system prices, sizes, stops
and sends (execution/bookorders.py), exactly as for the operator's picks.

ITS OWN JUDGEMENT, NOT A COPY OF MOMENTUM (2026-10-07)
The first prompt told it "what has held up here: buying 12-1 momentum", and it
picked its five highest-momentum names -- the momentum rule on another list.
Copying a rule cannot beat that rule, so the prompt now says so, keeps the
tested record as context, and tells it that churn costs money.

THE MENU (2026-10-08, WORKLIST §50)
For each NEW buy it may also name a stop (tight/normal/wide), a target
(none/2R/3R/5R) and a size (small/normal/large). Still names, still enums:
the policy maps each name to a number and the system builds the bracket. A
held name keeps the bracket it was bought with. A missing or unknown choice
falls back to normal stop, no target, normal size.

EVIDENCE OR NO NEW BUY
A NEW name is bought only if its `evidence` occurs verbatim (whitespace and
case aside) in that name's own facts block and is long enough to say
something. A name already held is kept even if its quote fails: an
ungrounded answer may not CHANGE the book, and selling is a change.
A missing, malformed or unreachable answer leaves the book exactly as it is.

PURE. The script fetches facts, calls the model, and trades.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scanner.models import Frozen

MIN_EVIDENCE_CHARS = 15

CHARTER = (
    "You are the desk's junior trader, running your own real paper book (about "
    "{sleeve} of capital, at most {n} stocks). Your goal is to earn a real seat: "
    "on 2027-04-06 your book must have beaten BOTH the S&P 500 and the desk's "
    "momentum rule (its monthly top-10 record) over the same days, with its worst "
    "drawdown under 25%. If it "
    "has, you get a wider list and more capital; if not, you go back to advising. "
    "You choose which stocks to hold and, for each new buy, how tight its stop, "
    "where its target and how large its size, from a menu; the system turns those "
    "choices into prices and orders. You explain every call. Your objective: MAXIMISE PROFIT -- the "
    "most total return you can make -- while keeping that drawdown under 25%. "
    "You are also in a head-to-head "
    "competition: the operator runs their own book of hand-picked stocks under "
    "the same rules, and your scoreboard shows both books every morning. It is "
    "scored month by month (win or lose), then by quarter; the season ends Dec 31 "
    "and its winner gets 10 more points of the account for the next season, taken "
    "from the other book. Both books now run 50/50 of the capital; from 2027, at the end "
    "of each quarter the loser gives 25% of the capital to the winner (never past 75/25: the "
    "loser always keeps a book); this quarter is the trial run. A random 'ghost' book "
    "is also scored each month: beating "
    "it shows your picking, not your list, did the work."
)

RULES = "\n".join((
    "Rules:",
    "- Choose at most {n} stocks to HOLD from the list below. Fewer is fine; none is fine.",
    "- A stock you hold now and leave out will be SOLD today. A new stock you include "
    "will be BOUGHT today.",
    "- Every change costs money and resets the clock. Keep a holding unless its reason "
    "has changed; do not swap stocks just because another looks slightly better today.",
    "- For each, give a short reason and an 'evidence' field that copies an exact phrase "
    "from THAT stock's own block below. A new buy without an exact phrase is ignored.",
    "- Do not write prices, sizes or percentages of your own. Use only what is below.",
    "- Use your own judgement. The momentum rule picks the strongest 12-1 momentum "
    "names monthly (marked in the list when they are yours to choose); simply "
    "copying it cannot beat it. Momentum, "
    "news, earnings timing and the market trend are all yours to weigh. On this "
    "desk RSI, moving averages and chart patterns were tested and showed no edge.",
    "- An earnings report within days can gap a stock either way, past its stop.",
    "- 'calendar' and 'in the news' lines are context: news is usually in the price within "
    "minutes. Use them for timing and risk (an earnings date, CPI, the Fed), not as a "
    "reason to swap a holding.",
    "",
))


class Menu(Frozen):
    """The names Gemma may choose from; the numbers stay in the policy."""
    stops: tuple[str, ...]
    targets: tuple[str, ...]
    sizes: tuple[str, ...]


class Plan(Frozen):
    symbol: str
    stop: str = "normal"
    target: str = "none"
    size: str = "normal"


MENU_RULES = "\n".join((
    "Your menu, for your NEW buys (a stock you already hold keeps its stop and target):",
    "{menu_text}",
    "- Choose them from your reason: a wider stop gives a volatile idea room, a tighter one "
    "says exactly where you are wrong; a target takes profit, 'none' lets a winner run.",
    "- A wide stop is not a reason to keep a failed idea. If the reason has failed, leave "
    "the stock out and it is sold.",
    "- Large size is for your strongest evidence, small for an idea you are testing.",
    "",
))


#: Outside regular hours (WORKLIST §59): what a decision means right now.
SESSION_NOTE = (
    "Session: {session} (outside regular hours). Your buys and sells go out now as "
    "limit orders in thin trading; no stop can be placed until the 9:30 ET open, when "
    "your stop and target are attached. Weigh that: a new buy tonight has no stop "
    "until the open.\n")


class Decision(Frozen):
    keep: tuple[str, ...]
    plans: tuple[Plan, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    reasons: tuple[tuple[str, str], ...] = ()
    journal: str = ""
    unchanged: bool = False


def schema(allowed: Iterable[str], max_positions: int,
           menu: Menu | None = None) -> dict[str, Any]:
    """Grammar-constrained answer: symbols from the list, text only, and with a
    menu, the stop/target/size NAMES it offers."""
    item: dict[str, Any] = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "enum": list(allowed)},
            "reason": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["symbol", "reason", "evidence"],
    }
    if menu is not None:
        item = {**item,
                "properties": {**item["properties"],
                               "stop": {"type": "string", "enum": list(menu.stops)},
                               "target": {"type": "string", "enum": list(menu.targets)},
                               "size": {"type": "string", "enum": list(menu.sizes)}},
                "required": [*item["required"], "stop", "target", "size"]}
    return {
        "type": "object",
        "properties": {
            "holdings": {
                "type": "array", "maxItems": max_positions,
                "items": item,
            },
            "journal": {"type": "string"},
        },
        "required": ["holdings", "journal"],
    }


def prompt(facts: Mapping[str, str], *, held: Mapping[str, str], max_positions: int,
           scoreboard: str = "", sleeve: str = "its share",
           menu: Menu | None = None, menu_text: str = "", session: str = "regular") -> str:
    book = "\n".join(f"- {s}: {status}" for s, status in held.items()) or "- (no positions)"
    score = scoreboard or "No scoreboard yet: this is the start of your record."
    blocks = "\n\n".join(f"[{s}]\n{text}" for s, text in facts.items())
    return "\n".join((
        CHARTER.format(sleeve=sleeve, n=max_positions), "",
        RULES.format(n=max_positions),
        MENU_RULES.format(menu_text=menu_text) if menu is not None else "",
        SESSION_NOTE.format(session=session) if session != "regular" else "",
        f"Your scoreboard: {score}", "",
        "Your book now:", book, "",
        "The list:", "", blocks, "",
        "Answer with your holdings" + (", each with its stop, target and size," if menu
                                         is not None else "") + " and a two-sentence 'journal' entry on today's thinking.",
    ))


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def grounded(evidence: str, block: str) -> bool:
    quote = _norm(evidence)
    return len(quote) >= MIN_EVIDENCE_CHARS and quote in _norm(block)


def _plan(item: Mapping[str, Any], symbol: str, menu: Menu | None) -> Plan:
    if menu is None:
        return Plan(symbol=symbol)
    def pick(key: str, allowed: tuple[str, ...], default: str) -> str:
        value = str(item.get(key, ""))
        return value if value in allowed else default
    return Plan(symbol=symbol, stop=pick("stop", menu.stops, "normal"),
                target=pick("target", menu.targets, "none"),
                size=pick("size", menu.sizes, "normal"))


def validate(answer: Any, facts: Mapping[str, str], *, held: set[str],
             max_positions: int, menu: Menu | None = None) -> Decision:
    """The holdings to keep after every rule above. Never raises."""
    items = answer.get("holdings") if isinstance(answer, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return Decision(keep=tuple(sorted(held)), unchanged=True,
                        journal="no usable answer -- book left unchanged")
    keep: list[str] = []
    rejected: list[tuple[str, str]] = []
    reasons: list[tuple[str, str]] = []
    plans: list[Plan] = []
    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        if symbol in keep:
            continue
        if symbol not in facts:
            rejected.append((symbol, "not on the list"))
            continue
        if len(keep) >= max_positions:
            rejected.append((symbol, "over the limit"))
            continue
        if symbol not in held and not grounded(item.get("evidence", ""), facts[symbol]):
            rejected.append((symbol, "evidence not found in its facts"))
            continue
        keep.append(symbol)
        reasons.append((symbol, str(item.get("reason", ""))[:300]))
        plans.append(_plan(item, symbol, menu))
    return Decision(keep=tuple(keep), rejected=tuple(rejected), reasons=tuple(reasons),
                    plans=tuple(plans),
                    journal=str(answer.get("journal", ""))[:600])


def diff(keep: Iterable[str], held: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(buys, sells) to move the book from `held` to `keep`."""
    keep, held = tuple(keep), set(held)
    return (tuple(s for s in keep if s not in held),
            tuple(sorted(s for s in held if s not in set(keep))))
