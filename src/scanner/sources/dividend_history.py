"""What a company has actually paid, and whether it has kept raising it.

WHY HISTORY AND NOT YIELD
Yield is a ratio, and it rises when the denominator collapses. Screening on
"high yield" selects for falling prices — the classic trap, and it is visible
in real data:

    T    4.7%   2022:1.35  2023:1.11  2024:1.11  2025:1.11   cut, then frozen
    MO   6.3%   2022:3.68  2023:3.84  2024:4.00  2025:4.16   raised every year
    KO   2.4%   2022:1.76  2023:1.84  2024:1.94  2025:2.04   raised every year

AT&T and Altria look similar on a yield screen. They are not similar. The
signal is in the payment history, so this module reads that and treats yield as
one input rather than the answer.

THE MONTHLY-PAYER TRAP
Trailing yield is computed over a DATE WINDOW, never over a payment count.
"Last four payments" is a year for a quarterly payer and a third of a year for
a monthly one: Realty Income, which pays monthly, reports 1.7% under the naive
method against a true yield near 5.6%. That error understates the best income
names by a factor of three and would quietly steer a screen away from them.

WHAT IS DELIBERATELY NOT HERE
Payout ratio. It needs earnings data this project does not carry, and a payout
ratio computed from a bad EPS figure is worse than no payout ratio at all —
it looks authoritative. Coverage is a real question; it is left to a human
until the data supports answering it honestly.
"""

from __future__ import annotations

import collections
from datetime import date, datetime, timedelta, timezone

import httpx

from scanner.models import Frozen

_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

#: Payments per year that identify a schedule. Anything else is "irregular",
#: which is itself informative — special dividends and erratic payers should
#: not be ranked beside a company that has paid every quarter for a decade.
_FREQUENCIES = {12: "monthly", 4: "quarterly", 2: "semi-annual", 1: "annual"}


class Payment(Frozen):
    paid_on: date
    amount: float


class DividendHistory(Frozen):
    """Every dividend a company has paid in the fetched window."""

    symbol: str
    price: float
    payments: tuple[Payment, ...] = ()

    # --- yield ------------------------------------------------------------

    def trailing_12m(self, today: date | None = None) -> float:
        """Cash paid per share over the last 365 days.

        A date window, not a payment count — see the module docstring.
        """
        end = today or date.today()
        start = end - timedelta(days=365)
        return sum(p.amount for p in self.payments if start < p.paid_on <= end)

    def yield_pct(self, today: date | None = None) -> float:
        if self.price <= 0:
            return 0.0
        return self.trailing_12m(today) / self.price * 100.0

    # --- history ----------------------------------------------------------

    def by_year(self) -> dict[int, float]:
        """Total paid per calendar year."""
        totals: dict[int, float] = collections.defaultdict(float)
        for payment in self.payments:
            totals[payment.paid_on.year] += payment.amount
        return dict(totals)

    def complete_years(self) -> dict[int, float]:
        """Annual payout per whole calendar year, normalised for schedule drift.

        Two corrections, both of which invented cuts that never happened:

        PARTIAL YEARS. The first year of a fetched window opens mid-year and
        the current year is always incomplete, so both are dropped.

        PAYMENT-COUNT DRIFT. A monthly payer does not reliably pay twelve
        times per calendar year — a December declaration can settle in
        January. Realty Income paid 11 times in 2024 and 13 in 2025, so its
        raw totals read 3.06 -> 2.87 -> 3.49 and the screen called a company
        that has raised its dividend for twenty-five years a CUTTER, then
        rejected it. Per-payment rates over the same span rise monotonically:
        0.2552 -> 0.2611 -> 0.2685.

        So each year is reported as its mean payment scaled to the schedule's
        usual count. That measures the RATE the company declared rather than
        how many cheques happened to clear inside a calendar boundary.
        """
        totals = self.by_year()
        if len(totals) < 3:
            return {}
        years = sorted(totals)[1:-1]
        counts = collections.Counter(
            p.paid_on.year for p in self.payments if p.paid_on.year in set(years)
        )
        if not counts:
            return {}
        # The schedule's usual cadence — the mode, not the mean, so one odd
        # year cannot drag the baseline it is being measured against.
        usual = collections.Counter(counts.values()).most_common(1)[0][0]
        return {
            y: totals[y] / counts[y] * usual if counts.get(y) else totals[y]
            for y in years
        }

    def growth_streak(self) -> int:
        """Consecutive complete years of a higher payout, counting back."""
        totals = self.complete_years()
        years = sorted(totals)
        streak = 0
        for earlier, later in zip(reversed(years[:-1]), reversed(years[1:])):
            if totals[later] > totals[earlier]:
                streak += 1
            else:
                break
        return streak

    def was_cut(self) -> bool:
        """Whether any complete year paid less than the year before it."""
        totals = self.complete_years()
        years = sorted(totals)
        return any(totals[b] < totals[a] for a, b in zip(years, years[1:]))

    def growth_pct(self) -> float:
        """Annualised growth across the complete years, as a percentage."""
        totals = self.complete_years()
        years = sorted(totals)
        if len(years) < 2 or totals[years[0]] <= 0:
            return 0.0
        spans = len(years) - 1
        return ((totals[years[-1]] / totals[years[0]]) ** (1 / spans) - 1) * 100.0

    # --- schedule ---------------------------------------------------------

    def payments_per_year(self) -> float:
        totals = self.complete_years()
        if not totals:
            return 0.0
        counted = collections.Counter(
            p.paid_on.year for p in self.payments if p.paid_on.year in totals
        )
        return sum(counted.values()) / len(totals)

    def frequency(self) -> str:
        rate = self.payments_per_year()
        if rate <= 0:
            return "none"
        nearest = min(_FREQUENCIES, key=lambda n: abs(n - rate))
        return _FREQUENCIES[nearest] if abs(nearest - rate) <= 0.5 else "irregular"

    @property
    def pays_dividends(self) -> bool:
        return bool(self.payments)


def parse(symbol: str, payload: object) -> DividendHistory | None:
    """Read a Yahoo chart payload. Returns None rather than raising."""
    try:
        result = payload["chart"]["result"][0]        # type: ignore[index]
        price = float(result["meta"]["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    raw = (result.get("events") or {}).get("dividends") or {}
    payments: list[Payment] = []
    for entry in raw.values():
        try:
            when = datetime.fromtimestamp(int(entry["date"]), timezone.utc).date()
            amount = float(entry["amount"])
        except (KeyError, TypeError, ValueError, OSError):
            continue
        if amount > 0:
            payments.append(Payment(paid_on=when, amount=amount))

    return DividendHistory(
        symbol=symbol.upper(), price=price,
        payments=tuple(sorted(payments, key=lambda p: p.paid_on)),
    )


async def fetch(symbol: str, client: httpx.AsyncClient, *, range_: str = "10y"
                ) -> DividendHistory | None:
    """Dividend history for one symbol, or None if it cannot be read."""
    try:
        response = await client.get(
            _CHART.format(symbol=symbol.replace(".", "-")),
            params={"range": range_, "interval": "1d", "events": "div"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if response.status_code != 200:
            return None
        return parse(symbol, response.json())
    except (httpx.HTTPError, ValueError):
        return None
