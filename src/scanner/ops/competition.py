"""The competition: the operator's picks vs Gemma, scored monthly (WORKLIST §46).

RULES, SET 2026-10-07 BEFORE ANY MONTH WAS SCORED
- Competitors: PICKS (operator) and GEMMA, the real books; GHOST_PICKS and
  GHOST_GEMMA, random paper books drawn each month (8 names from the 150 most
  traded S&P 500 stocks; 5 from Gemma's own menu) with the same 2.5-ATR stop;
  the MOMENTUM rule's top 10 held on paper all month; the S&P 500.
- Monthly score: the change in a book's total P/L (realised + unrealised)
  over the month, divided by its average money invested. Paper books: the
  equal-weight return of their names, stops applied on daily lows.
- Month: the higher of PICKS and GEMMA wins it. Each also "beat its ghost"
  or not -- that says whether the picking was skill or the list.
- Quarter: more months won; a tie goes to the higher quarterly return.
- Season = calendar year (season 1: 2026-10-07 .. 2026-12-31). The winner is
  the higher season return. REWARD: +10 percentage points of the account for
  the next season, taken from the other book, applied as a new window.

EXCLUSIONS (operator 2026-10-21: "this test shouldnt be counted as a challenge"):
data/competition/excluded.json names a book, a symbol and a window; a position
of that book in that symbol inside the window, and any closed trade of it
ENTERED inside the window, is left out of the score. For declared tests only.

PURE. The script takes snapshots and fetches prices.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from scanner.models import Frozen

PICKS, GEMMA = "picks", "gemma"
GHOST_PICKS, GHOST_GEMMA, MOMENTUM, SPX = "ghost_picks", "ghost_gemma", "momentum_rule", "sp500"
REWARD_POINTS = 10.0
#: THE STAKES (operator, 2026-10-09; WORKLIST §60). Months give points; a
#: quarter goes to the most points (tie: higher total return). From 2027-01-01
#: the two books (already EVEN at 50/50 since 2026-10-09) start the stakes year at
#: 50/50, and at the end of each quarter the loser gives
#: 25% of the capital to the winner, never past 75/25 (the loser keeps a book, and
#: a comeback is one quarter away). Q4 2026 is the trial quarter: its winner takes
#: the existing +10 points.
QUARTER_TRANSFER_PCT = 25.0
CAPITAL_FLOOR_PCT = 25.0
STAKES_START = date(2027, 1, 1)
STAKES_START_SPLIT = 50.0


def split_after(quarter_winners: Sequence[str | None],
                start_picks_pct: float = STAKES_START_SPLIT) -> tuple[float, float]:
    """(picks %, Gemma %) after these quarters, in order. None = undecided quarter."""
    picks = start_picks_pct
    for w in quarter_winners:
        if w == PICKS:
            picks += QUARTER_TRANSFER_PCT
        elif w == GEMMA:
            picks -= QUARTER_TRANSFER_PCT
        picks = min(100.0 - CAPITAL_FLOOR_PCT, max(CAPITAL_FLOOR_PCT, picks))
    return picks, 100.0 - picks


class Snapshot(Frozen):
    """One evening's state of one real book."""
    day: date
    book: str
    pl_total: float          # realised to date + unrealised now, dollars
    invested: float          # cost basis of open positions, dollars


class PaperName(Frozen):
    symbol: str
    entry: float
    stop: float | None = None
    exit_day: date | None = None
    exit_price: float | None = None


class PaperBook(Frozen):
    name: str
    month: str               # "2026-10"
    start: date
    names: tuple[PaperName, ...]


def month_of(day: date) -> str:
    return f"{day:%Y-%m}"


def quarter_of(month: str) -> str:
    y, m = month.split("-")
    return f"{y}-Q{(int(m) - 1) // 3 + 1}"


def draw(pool: Sequence[str], n: int, month: str, who: str) -> tuple[str, ...]:
    """Reproducible random draw: the same month and book always draw the same names."""
    pool = sorted(set(pool))
    return tuple(sorted(random.Random(f"{who}:{month}").sample(pool, min(n, len(pool)))))


def advance(book: PaperBook, bars: Mapping[str, Sequence[tuple[date, float, float, float]]]
            ) -> PaperBook:
    """Apply stops through the latest bar. bars[s] = [(day, open, low, close), ...]
    oldest first. A day whose low reaches the stop exits at the stop, or at the
    open if it gapped below. Never changes a name already exited."""
    out = []
    for n in book.names:
        if n.exit_day is not None or n.stop is None:
            out.append(n)
            continue
        hit = None
        for d, o, low, _ in bars.get(n.symbol, ()):
            if d > book.start and low <= n.stop:
                hit = (d, min(o, n.stop))
                break
        out.append(n if hit is None else n.model_copy(update={"exit_day": hit[0], "exit_price": hit[1]}))
    return book.model_copy(update={"names": tuple(out)})


def paper_return(book: PaperBook, last: Mapping[str, float], *, upto: date | None = None) -> float:
    """Equal-weight % return: exit price if stopped (on or before `upto`), else the last close."""
    rets = []
    for n in book.names:
        stopped = n.exit_day is not None and (upto is None or n.exit_day <= upto)
        px = n.exit_price if stopped else last.get(n.symbol)
        if px is not None and n.entry:
            rets.append(px / n.entry - 1)
    return sum(rets) / len(rets) * 100 if rets else 0.0


def book_return(snaps: Iterable[Snapshot], book: str, first: date, last: date) -> float | None:
    """% for a real book between two days: change in total P/L over average money invested."""
    s = sorted((x for x in snaps if x.book == book and first <= x.day <= last), key=lambda x: x.day)
    if len(s) < 2:
        return None
    invested = [x.invested for x in s if x.invested > 0]
    if not invested:
        return None
    return (s[-1].pl_total - s[0].pl_total) / (sum(invested) / len(invested)) * 100


def month_to_date(snaps: Iterable[Snapshot], month: str) -> dict[str, float]:
    """Each real book's return so far in `month`, scored exactly as the
    standings score it (scripts/competition.py): every book anchored at P/L 0
    the day before its first snapshot, then book_return from the last day
    before the month (or its first day) to its latest. A book that holds
    nothing still has a score -- its realised P/L over what it had invested --
    which is what Gemma's scoreboard missed before 2026-10-18 (it showed +0.00%)."""
    snaps = list(snaps)
    anchored = list(snaps)
    for book in (PICKS, GEMMA):
        mine = sorted((s for s in snaps if s.book == book), key=lambda s: s.day)
        if mine:
            anchored.append(Snapshot(day=date.fromordinal(mine[0].day.toordinal() - 1),
                                     book=book, pl_total=0.0, invested=mine[0].invested))
    days = sorted({s.day for s in anchored if month_of(s.day) == month})
    if not days:
        return {}
    before = [s.day for s in anchored if month_of(s.day) < month]
    first = max(before) if before else days[0]
    out = {}
    for book in (PICKS, GEMMA):
        r = book_return(anchored, book, first, days[-1])
        if r is not None:
            out[book] = r
    return out


def load_snapshots(text: str) -> list[Snapshot]:
    """snapshots.jsonl text -> snapshots; a re-run on the same day replaces."""
    last: dict[tuple, Snapshot] = {}
    for line in text.splitlines():
        if line.strip():
            s = Snapshot.model_validate_json(line)
            last[(s.day, s.book)] = s
    return list(last.values())


class MonthResult(Frozen):
    month: str
    scores: dict[str, float]
    winner: str | None


def month_winner(scores: Mapping[str, float]) -> str | None:
    a, b = scores.get(PICKS), scores.get(GEMMA)
    if a is None or b is None or a == b:
        return None
    return PICKS if a > b else GEMMA


def quarter_winner(months: Sequence[MonthResult]) -> str | None:
    wins = {PICKS: 0, GEMMA: 0}
    for m in months:
        if m.winner:
            wins[m.winner] += 1
    if wins[PICKS] != wins[GEMMA]:
        return max(wins, key=wins.get)
    total = {k: sum(m.scores.get(k, 0.0) for m in months) for k in (PICKS, GEMMA)}
    if total[PICKS] == total[GEMMA]:
        return None
    return max(total, key=total.get)


# --- how sure is the lead? (Jansen ch. 10: compare return streams with their
# uncertainty, not "who is ahead"; a bootstrap here instead of PyMC3) ---------
MIN_DAYS_FOR_PROBABILITY = 10


def daily_returns(snaps: Iterable[Snapshot], book: str) -> dict[date, float]:
    """{day: % return} from consecutive snapshots: change in total P/L over the
    previous day's money invested."""
    s = sorted((x for x in snaps if x.book == book), key=lambda x: x.day)
    out = {}
    for prev, cur in zip(s, s[1:]):
        if prev.invested > 0:
            out[cur.day] = (cur.pl_total - prev.pl_total) / prev.invested * 100
    return out


def prob_better(a: Mapping[date, float], b: Mapping[date, float], *, draws: int = 5000,
                seed: int = 0) -> float | None:
    """Probability that A's true daily edge over B is positive: resample the
    paired daily differences with replacement and count how often their mean
    is above zero. None until MIN_DAYS_FOR_PROBABILITY shared days exist."""
    days = sorted(set(a) & set(b))
    if len(days) < MIN_DAYS_FOR_PROBABILITY:
        return None
    diffs = [a[d] - b[d] for d in days]
    rng = random.Random(seed)
    n = len(diffs)
    wins = sum(1 for _ in range(draws) if sum(rng.choice(diffs) for _ in range(n)) > 0)
    return wins / draws


class Exclusion(Frozen):
    """A declared test the challenge does not count."""
    book: str
    symbol: str
    start: date
    until: date
    reason: str


def read_exclusions(text: str) -> list[Exclusion]:
    try:
        raw = json.loads(text)
    except ValueError:
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        try:
            out.append(Exclusion(book=str(r["book"]), symbol=str(r["symbol"]).upper(),
                                 start=date.fromisoformat(r["from"]),
                                 until=date.fromisoformat(r["until"]), reason=str(r["reason"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def is_excluded(book: str, symbol: str, day: date, rules: Iterable[Exclusion]) -> bool:
    return any(r.book == book and r.symbol == symbol and r.start <= day <= r.until for r in rules)


def without_excluded(rows: Iterable[Mapping[str, Any]],
                     rules: Sequence[Exclusion]) -> list[Mapping[str, Any]]:
    """Audit rows minus the closed trades of a declared test, matched by book
    (client-id prefix), symbol and ENTRY day (the client id's timestamp)."""
    from scanner.execution.ownership import strategy_of

    out = []
    for row in rows:
        o = row.get("outcome") or {} if row.get("event") == "trade_outcome" else {}
        coid = str(o.get("client_order_id") or "")
        if coid and rules:
            try:
                entered = datetime.fromtimestamp(int(coid.rsplit("-", 1)[1])).date()
            except (IndexError, ValueError):
                entered = None
            if entered and is_excluded(strategy_of(coid), str(o.get("symbol") or ""), entered, rules):
                continue
        out.append(row)
    return out
