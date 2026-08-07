"""Ex-dividend dates, and the mechanical price drop that comes with them.

TWO CONSUMERS, ONE FACT
The income sleeve wants to know when it gets paid. The swing strategy needs to
know something less obvious: on the ex-dividend morning the exchange reduces
the opening price by the dividend, automatically. That is not a market move and
no analysis predicts it — it is an accounting adjustment applied to every
resting order overnight.

    KO   drop 0.61%  =  41% of a 1.5% stop
    MO   drop 1.56%  = 104%
    PFE  drop 1.64%  = 109%
    VZ   drop 1.51%  = 100%

A long position in MO held through its ex-date is stopped out by arithmetic.
The backtest never saw this, because it ran on Yahoo's split- and
dividend-adjusted bars, where the drop has been retroactively smoothed away.
Live bars are not adjusted. This is a real gap between what was measured and
what will happen, and it is the reason this module exists.

WHERE EACH FACT COMES FROM, AND WHY IT TAKES TWO SOURCES
Alpaca's announcements endpoint is authoritative for what is *coming*, and
useless for what has *been*:

    - `since`/`until` filter PAYABLE date, not ex-date. A September window
      returned 51 records, all with September payable dates and NONE with a
      September ex-date. Filtering a date range and reading `ex_date` from the
      result is a mistake that looks like it works.
    - It returns nothing historical. May through August: zero records.
    - Forward visibility is about one month, because that is when boards
      declare. Ninety days of window yielded future ex-dates in August only.
    - `old_rate`/`new_rate` are split ratios, 1/1 on every cash dividend. They
      are NOT the previous and new dividend amount, and cannot detect a raise
      or a cut.

So: Alpaca for the forward calendar, Yahoo's payment history for everything
retrospective — yield, growth streaks, and whether a payout was ever cut.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from scanner.models import Frozen

_ANNOUNCEMENTS = "/v2/corporate_actions/announcements"

#: Alpaca rejects windows longer than this.
MAX_WINDOW_DAYS = 90


class DividendEvent(Frozen):
    """One declared cash dividend."""

    symbol: str
    cash: float
    ex_date: date
    payable_date: date | None = None
    record_date: date | None = None

    def drop_pct(self, price: float) -> float:
        """The mechanical ex-morning drop, as a percentage of price."""
        if price <= 0:
            return 0.0
        return self.cash / price * 100.0

    def erodes_stop(self, price: float, stop_pct: float,
                    threshold: float = 0.33) -> bool:
        """Whether the drop eats enough of the stop to matter.

        A third is the default because a stop already absorbing normal noise
        has little room left for a guaranteed adjustment on top. This reports;
        it does not decide.
        """
        if stop_pct <= 0:
            return False
        return self.drop_pct(price) / stop_pct >= threshold


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse(records: object, *, on_or_after: date | None = None
          ) -> tuple[DividendEvent, ...]:
    """Validated events, newest last. Junk is dropped, never guessed at.

    Of 650 live records, 81 carried a cash amount of zero and 3 had no ex-date
    at all. A zero-cash dividend would report a 0% drop and silently wave
    through the very position this module exists to flag, so both are dropped
    rather than defaulted.
    """
    if not isinstance(records, list):
        return ()
    events: list[DividendEvent] = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("initiating_symbol") or "").upper()
        ex_date = _parse_date(raw.get("ex_date"))
        try:
            cash = float(raw.get("cash") or 0.0)
        except (TypeError, ValueError):
            continue
        if not symbol or ex_date is None or cash <= 0:
            continue
        if on_or_after is not None and ex_date < on_or_after:
            continue
        events.append(DividendEvent(
            symbol=symbol, cash=cash, ex_date=ex_date,
            payable_date=_parse_date(raw.get("payable_date")),
            record_date=_parse_date(raw.get("record_date")),
        ))
    return tuple(sorted(events, key=lambda e: (e.ex_date, e.symbol)))


async def fetch_announcements(
    client: httpx.AsyncClient,
    key_id: str,
    secret_key: str,
    *,
    host: str = "https://paper-api.alpaca.markets",
    days: int = MAX_WINDOW_DAYS,
    today: date | None = None,
) -> tuple[DividendEvent, ...]:
    """Declared cash dividends with an ex-date still ahead of us.

    The window is generous because `since`/`until` bound the PAYABLE date: an
    ex-date next week is often paid a month later, so a narrow window silently
    hides the events that matter most. Returns empty on any failure — a missing
    calendar must never look like "no dividend coming", so callers treat an
    empty result as unknown rather than clear.
    """
    start = today or date.today()
    params = {
        "ca_types": "dividend",
        "since": start.isoformat(),
        "until": (start + timedelta(days=min(days, MAX_WINDOW_DAYS))).isoformat(),
    }
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
    try:
        response = await client.get(f"{host}{_ANNOUNCEMENTS}",
                                    headers=headers, params=params)
        if response.status_code != 200:
            return ()
        return parse(response.json(), on_or_after=start)
    except (httpx.HTTPError, ValueError):
        return ()


def next_ex_date(events: tuple[DividendEvent, ...], symbol: str
                 ) -> DividendEvent | None:
    """The soonest upcoming ex-dividend for one symbol, if any is known."""
    upcoming = [e for e in events if e.symbol == symbol.upper()]
    return min(upcoming, key=lambda e: e.ex_date) if upcoming else None


def by_symbol(events: tuple[DividendEvent, ...]) -> dict[str, DividendEvent]:
    """Soonest upcoming ex-dividend per symbol — the lookup a scan needs."""
    soonest: dict[str, DividendEvent] = {}
    for event in events:
        current = soonest.get(event.symbol)
        if current is None or event.ex_date < current.ex_date:
            soonest[event.symbol] = event
    return soonest
