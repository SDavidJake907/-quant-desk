"""Daily bars must carry ONE price scale.

Yahoo's chart response holds two. `indicators.adjclose` is corrected for splits
and dividends; `indicators.quote` (open/high/low/close) is the raw traded price.
Reading closes from the first and highs/lows from the second builds a bar whose
close can sit outside its own range — on MO, 95% of five years of daily bars.

Nothing caught it because the fixtures set adjclose == close, which is the one
case where the bug is invisible.

WHY IT IS NOT COSMETIC
`backtest.setups.resolve` decides a trade by comparing a stop and a target
against `bars.lows` and `bars.highs`. Derive those levels from an adjusted
close and test them against raw highs and the target sits below almost every
bar's high: every trade resolves as an instant winner and the backtest reports
an edge that is purely a unit mismatch. A daily swing strategy is exactly the
thing that would be built on top of that number.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from scanner.features import bars as bars_module
from scanner.features.bars import fetch_bars

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _payload(*, closes, opens, highs, lows, adjcloses) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [1_700_000_000 + i * 86_400 for i in range(len(closes))],
                    "indicators": {
                        "adjclose": [{"adjclose": adjcloses}],
                        "quote": [{
                            "open": opens,
                            "close": closes,
                            "volume": [1_000_000] * len(closes),
                            "high": highs,
                            "low": lows,
                        }],
                    },
                }
            ]
        }
    }


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bars_module, "CACHE_DIR", tmp_path)
    return tmp_path


#: A dividend payer: the two oldest bars are marked down 10%, the newest is
#: unadjusted. This is the shape of every real dividend-paying series.
DIVIDEND_SERIES = dict(
    opens=[100.0, 102.0, 104.0],
    highs=[105.0, 107.0, 109.0],
    lows=[95.0, 97.0, 99.0],
    closes=[100.0, 102.0, 104.0],
    adjcloses=[90.0, 91.8, 104.0],
)


@respx.mock
async def test_close_never_falls_outside_its_own_range(cache_dir):
    respx.get(CHART.format(symbol="MO")).mock(
        return_value=httpx.Response(200, json=_payload(**DIVIDEND_SERIES))
    )
    async with httpx.AsyncClient() as client:
        bars = await fetch_bars("MO", client, range_="1y")

    for i, (low, close, high) in enumerate(zip(bars.lows, bars.closes, bars.highs)):
        assert low <= close <= high, (
            f"bar {i}: close {close} outside [{low}, {high}] — "
            "adjusted and raw prices have been mixed"
        )


@respx.mock
async def test_open_is_carried_and_adjusted(cache_dir):
    """`resolve` reads `bars.opens` to model gap fills. Daily bars never had
    the field, so the whole gap path was unreachable on the daily timeframe."""
    respx.get(CHART.format(symbol="MO")).mock(
        return_value=httpx.Response(200, json=_payload(**DIVIDEND_SERIES))
    )
    async with httpx.AsyncClient() as client:
        bars = await fetch_bars("MO", client, range_="1y")

    assert len(bars.opens) == len(bars.closes)
    for i, (low, open_, high) in enumerate(zip(bars.lows, bars.opens, bars.highs)):
        assert low <= open_ <= high, f"bar {i}: open {open_} outside [{low}, {high}]"


@respx.mock
async def test_adjustment_preserves_the_shape_of_each_bar(cache_dir):
    """Scaling must be one ratio per bar applied to all four prices. If each
    field were adjusted differently the bar's proportions would change, and
    ATR — a high-minus-low measure — would drift for no market reason."""
    respx.get(CHART.format(symbol="MO")).mock(
        return_value=httpx.Response(200, json=_payload(**DIVIDEND_SERIES))
    )
    async with httpx.AsyncClient() as client:
        bars = await fetch_bars("MO", client, range_="1y")

    # Bar 0: raw 95/100/105 marked to an adjusted close of 90 — a 0.9 ratio.
    assert bars.closes[0] == pytest.approx(90.0)
    assert bars.lows[0] == pytest.approx(85.5)
    assert bars.highs[0] == pytest.approx(94.5)
    assert bars.opens[0] == pytest.approx(90.0)

    # The newest bar needs no adjustment and must survive untouched.
    assert bars.closes[-1] == pytest.approx(104.0)
    assert bars.lows[-1] == pytest.approx(99.0)
    assert bars.highs[-1] == pytest.approx(109.0)


@respx.mock
async def test_missing_open_falls_back_without_breaking_the_range(cache_dir):
    """Yahoo omits fields on thin names. A fallback must still land inside the
    bar, or it reintroduces the very inconsistency this module now forbids."""
    payload = _payload(**DIVIDEND_SERIES)
    del payload["chart"]["result"][0]["indicators"]["quote"][0]["open"]
    respx.get(CHART.format(symbol="THIN")).mock(
        return_value=httpx.Response(200, json=payload)
    )
    async with httpx.AsyncClient() as client:
        bars = await fetch_bars("THIN", client, range_="1y")

    for low, open_, high in zip(bars.lows, bars.opens, bars.highs):
        assert low <= open_ <= high


@respx.mock
async def test_bar_with_no_establishable_scale_is_dropped(cache_dir):
    """A zero raw close makes the ratio undefined, and there is no safe guess:
    keeping the raw high and low next to an adjusted close would rebuild the
    exact inconsistency this module forbids. Losing one defective bar is the
    cheaper error, and it must not divide by zero on the way."""
    series = dict(DIVIDEND_SERIES)
    series["closes"] = [0.0, 102.0, 104.0]
    respx.get(CHART.format(symbol="BAD")).mock(
        return_value=httpx.Response(200, json=_payload(**series))
    )
    async with httpx.AsyncClient() as client:
        bars = await fetch_bars("BAD", client, range_="1y")

    assert len(bars) == 2, "the unscalable bar should be gone, the rest kept"
    for low, close, high in zip(bars.lows, bars.closes, bars.highs):
        assert low <= close <= high
