"""wall_street: hard Street news for Gemma's brief, opinion and noise left out."""

from datetime import datetime

import pytest

from scanner.advisor import wall_street as ws
from scanner.ops import week_ahead as wa


@pytest.mark.parametrize("headline, kind", [
    ("Morgan Stanley Maintains Overweight on Micron, Raises Price Target to $1,050", "rating"),
    ("Citigroup Downgrades Fiserv to Neutral", "rating"),
    ("Merck To Acquire Verona Pharma For $10B", "deal"),
    ("Apple Announces $110B Buyback", "deal"),
    ("Valero Raises FY Guidance", "guidance"),
    ("Valero Energy Q3 EPS $3.12 Beats $2.85 Estimate", "results"),
    ("Fed's Waller Says Rate Cuts Still Likely This Year", "fed"),
])
def test_hard_news_is_kept_with_its_kind(headline, kind):
    assert ws.kind_of(headline) == kind


@pytest.mark.parametrize("headline", [
    "Is Nvidia Stock a Buy Now?",
    "3 Stocks to Buy Before Earnings Season",
    "This Week in TSLA: Roadster's Flying Debut, SpaceX Merger and More",
    "GE Aerospace Stock Nears Death Cross Amid Strong Earnings Outlook",
    "Saylor's BTC Price Target Raised",
    "Top US Grocery Chains' Websites Remain Offline",
    "CoreWeave Stock Forms H&S Pattern as Key Bond Yield Hits 15%",
    "S&P 500 Index Outlook: Strong Earnings Meet AI Rout",
])
def test_opinion_roundups_chart_lore_and_crypto_are_dropped(headline):
    assert ws.kind_of(headline) is None


def test_pick_puts_tradable_names_first_and_dedupes():
    items = [
        {"headline": "Citigroup Raises Price Target on Disney to $140", "symbols": ["DIS"]},
        {"headline": "Barclays Upgrades Micron to Overweight", "symbols": ["MU"]},
        {"headline": "Barclays Upgrades Micron to Overweight", "symbols": ["MU"]},
        {"headline": "Is Micron a Buy?", "symbols": ["MU"]},
    ]
    lines = ws.pick(items, tradable=["MU"])
    assert lines == ["[rating] MU: Barclays Upgrades Micron to Overweight",
                     "[rating] DIS: Citigroup Raises Price Target on Disney to $140"]


def test_universe_drops_microcaps_but_keeps_the_fed():
    items = [
        {"headline": "Rubico Announces $8.5M Registered Direct Offering", "symbols": ["RUBI"]},
        {"headline": "Treasury Yields Jump After Strong Payrolls", "symbols": ["TLT", "XYZ"]},
        {"headline": "Lilly Takes 19.9% Stake In TRex Bio", "symbols": ["LLY", "TRX"]},
    ]
    lines = ws.pick(items, tradable=[], universe=["LLY", "MU"])
    assert lines == ["[fed] TLT,XYZ: Treasury Yields Jump After Strong Payrolls",
                     "[deal] LLY: Lilly Takes 19.9% Stake In TRex Bio"]


def test_pick_respects_limit():
    items = [{"headline": f"Firm {i} Upgrades Micron", "symbols": ["MU"]} for i in range(20)]
    assert len(ws.pick(items, ["MU"], limit=8)) == 8


def test_window_reaches_back_to_friday_over_the_weekend():
    assert ws.window_hours(datetime(2026, 10, 19, 3)) == 72     # Monday
    assert ws.window_hours(datetime(2026, 10, 18, 3)) == 48     # Sunday
    assert ws.window_hours(datetime(2026, 10, 20, 3)) == 24     # Tuesday


def test_market_text_carries_wall_street_lines():
    text = wa.market_text([], president_lines=[], headlines=[],
                          wall_street=["[rating] MU: Barclays Upgrades Micron to Overweight"])
    assert "Wall Street since the last session" in text
    assert "Barclays Upgrades Micron" in text
    assert "Wall Street" not in wa.market_text([], president_lines=[], headlines=[])
