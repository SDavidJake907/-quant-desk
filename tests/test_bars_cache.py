"""Bar-cache regression tests.

The disk cache exists to stay under Yahoo's daily request budget, which means
whatever it stores is what every later run sees — for the rest of the day, no
matter how many times you re-run. That makes a wrong cache write much more
expensive than a failed fetch.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from scanner.features import bars as bars_module
from scanner.features.bars import BarsError, fetch_bars

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _payload(closes: list[float]) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [1_700_000_000 + i * 86_400 for i in range(len(closes))],
                    "indicators": {
                        "adjclose": [{"adjclose": closes}],
                        "quote": [{
                            "close": closes,
                            "volume": [1_000_000] * len(closes),
                            "high": closes,
                            "low": closes,
                        }],
                    },
                }
            ]
        }
    }


EMPTY = {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bars_module, "CACHE_DIR", tmp_path)
    return tmp_path


@respx.mock
async def test_unusable_response_is_not_cached(cache_dir):
    """Yahoo answers 200 with an empty series for symbols it does not know.

    Caching that pins the failure in place for the rest of the day — through
    a restart, and through the very fix that would have cured it. This is how
    BF.B stayed missing after its ticker translation was corrected.
    """
    respx.get(CHART.format(symbol="BF-B")).mock(
        return_value=httpx.Response(200, json=EMPTY)
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(BarsError):
            await fetch_bars("BF.B", client, range_="5y")

    assert list(cache_dir.iterdir()) == []


@respx.mock
async def test_usable_response_is_cached_and_reused(cache_dir):
    route = respx.get(CHART.format(symbol="AAPL")).mock(
        return_value=httpx.Response(200, json=_payload([100.0, 101.0, 102.0]))
    )

    async with httpx.AsyncClient() as client:
        first = await fetch_bars("AAPL", client, range_="5y")
        second = await fetch_bars("AAPL", client, range_="5y")

    assert route.call_count == 1  # second call served from disk
    assert first.closes == second.closes == (100.0, 101.0, 102.0)


@respx.mock
async def test_dotted_ticker_is_requested_with_a_dash(cache_dir):
    """The Bars keep the index spelling even though Yahoo is asked for BRK-B,
    so membership sets and price data stay joinable on the same symbol."""
    route = respx.get(CHART.format(symbol="BRK-B")).mock(
        return_value=httpx.Response(200, json=_payload([10.0, 11.0]))
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_bars("BRK.B", client, range_="5y")

    assert route.called
    assert result.symbol == "BRK.B"


@respx.mock
async def test_stale_cache_is_ignored(cache_dir):
    (cache_dir / "AAPL__5y.json").write_text(
        json.dumps({"cached_on": "1999-01-01", "payload": _payload([1.0, 2.0])}),
        encoding="utf-8",
    )
    respx.get(CHART.format(symbol="AAPL")).mock(
        return_value=httpx.Response(200, json=_payload([50.0, 51.0]))
    )

    async with httpx.AsyncClient() as client:
        result = await fetch_bars("AAPL", client, range_="5y")

    assert result.closes == (50.0, 51.0)


# ---- LLM memory hygiene ------------------------------------------------

def test_keep_alive_is_sent_so_models_can_be_unloaded():
    """Two models resident at once do not fit in 16GB beside the embedder.
    Benchmarking one against the other needs the first to let go."""
    import inspect

    from scanner.llm.ollama import chat_json

    assert "keep_alive" in inspect.signature(chat_json).parameters


# ---- intraday staleness ------------------------------------------------

@respx.mock
async def test_intraday_cache_expires_within_the_session(cache_dir, monkeypatch):
    """A date-stamped cache reads as fresh all day. Measured live on
    2026-08-06: a payload fetched at 03:49 was still served at 10:56, so the
    scanner saw the PREVIOUS session's closing bars while the market was open
    and would have priced orders from them."""
    import time as time_module

    from scanner.features import bars as bars_module

    real_time = time_module.time()
    route = respx.get(CHART.format(symbol="MU")).mock(
        return_value=httpx.Response(200, json=_payload([10.0, 11.0, 12.0]))
    )
    async with httpx.AsyncClient() as client:
        await bars_module.fetch_payload(
            "MU", client, range_="60d", interval="30m", ttl_seconds=1800
        )
        assert route.call_count == 1

        # …seven hours later, same calendar day
        monkeypatch.setattr(time_module, "time", lambda: real_time + 25_000)
        await bars_module.fetch_payload(
            "MU", client, range_="60d", interval="30m", ttl_seconds=1800
        )
    assert route.call_count == 2, "stale intraday payload was reused"


@respx.mock
async def test_daily_cache_still_lasts_the_whole_day(cache_dir):
    """Daily bars change once a day, so the date stamp remains correct there
    and re-runs must stay free."""
    route = respx.get(CHART.format(symbol="AAPL")).mock(
        return_value=httpx.Response(200, json=_payload([100.0, 101.0]))
    )
    async with httpx.AsyncClient() as client:
        await fetch_bars("AAPL", client, range_="5y")
        await fetch_bars("AAPL", client, range_="5y")
    assert route.call_count == 1


@respx.mock
async def test_a_cache_without_an_age_is_refetched(cache_dir):
    """Payloads written before ages were recorded cannot be aged, and
    guessing they are fresh is the bug this fix exists to remove."""
    import json as json_module

    (cache_dir / "MU__60d__30m.json").write_text(
        json_module.dumps({"cached_on": date.today().isoformat(),
                           "payload": _payload([1.0, 2.0])}),
        encoding="utf-8",
    )
    route = respx.get(CHART.format(symbol="MU")).mock(
        return_value=httpx.Response(200, json=_payload([50.0, 51.0]))
    )
    from scanner.features import bars as bars_module
    async with httpx.AsyncClient() as client:
        payload = await bars_module.fetch_payload(
            "MU", client, range_="60d", interval="30m", ttl_seconds=1800
        )
    assert route.called
    assert payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] == 50.0


def test_numeric_keep_alive_is_sent_as_a_number_not_a_string():
    """Ollama rejects "-1" with `time: missing unit in duration` while
    accepting -1. Settings arrive as strings, so an env value of -1 broke
    every triage call while the model sat loaded and idle."""
    from scanner.llm.ollama import coerce_keep_alive

    assert coerce_keep_alive("-1") == -1
    assert coerce_keep_alive("300") == 300
    assert coerce_keep_alive(-1) == -1
    assert coerce_keep_alive("5m") == "5m"
    assert coerce_keep_alive("10s") == "10s"
