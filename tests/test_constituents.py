"""Universe tests — the GOOG/GOOGL double-count regression."""

from __future__ import annotations

from scanner.sources.constituents import deduplicate_share_classes, parse_csv

CSV = """Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded
GOOGL,Alphabet Inc. Class A,Communication Services,Interactive Media,"Mountain View",2014-04-03,1652044,1998
GOOG,Alphabet Inc. Class C,Communication Services,Interactive Media,"Mountain View",2006-04-03,1652044,1998
AAPL,Apple Inc.,Information Technology,Technology Hardware,"Cupertino",1982-11-30,320193,1977
"""


def test_share_classes_collapse_to_one_company():
    rows = parse_csv(CSV)
    symbols = [r.symbol for r in rows]
    assert "GOOGL" in symbols
    assert "GOOG" not in symbols
    assert len(rows) == 2


def test_dedupe_can_be_disabled():
    assert len(parse_csv(CSV, dedupe=False)) == 3


def test_sector_and_cik_are_captured():
    apple = next(r for r in parse_csv(CSV) if r.symbol == "AAPL")
    assert apple.sector == "Information Technology"
    assert apple.cik == "320193"


def test_falls_back_to_name_when_cik_missing():
    from scanner.sources.constituents import Constituent

    rows = (
        Constituent(symbol="X", name="Same Co", sector="", cik=""),
        Constituent(symbol="X2", name="Same Co", sector="", cik=""),
    )
    assert len(deduplicate_share_classes(rows)) == 1
