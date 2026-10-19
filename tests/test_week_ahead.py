"""The daily brief (WORKLIST §52): a calendar of what is coming, today's news,
and which stocks each item touches -- written at 03:00, read by Gemma at 05:45."""

from __future__ import annotations

from datetime import date, datetime, timezone

from scanner.advisor import news_impact as ni
from scanner.ops import week_ahead as wa
from scanner.sources import president as pres
from scanner.sources.calendar import Earnings

MON = date(2026, 10, 12)
DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]


# --- calendar ---------------------------------------------------------------

def test_events_merge_econ_earnings_and_fixed_dates_in_order():
    econ = {date(2026, 10, 14): ("CPI (Sep)",), date(2026, 10, 15): ("PPI MoM (Sep)",)}
    earn = {"JPM": Earnings(symbol="JPM", on=date(2026, 10, 13), when="pre-mkt"),
            "TXN": Earnings(symbol="TXN", on=date(2026, 10, 21), when="after-close")}
    fixed = ((date(2026, 10, 27), "fed", "FOMC meeting (Oct 27-28)"),
             (date(2026, 12, 8), "fed", "FOMC meeting (Dec 8-9)"))
    events = wa.events(DAYS, econ=econ, earnings=earn, fixed=fixed)
    assert [e.day for e in events] == sorted(e.day for e in events)
    assert any(e.kind == "earnings" and e.symbols == ("JPM",) for e in events)
    assert any(e.kind == "econ" and "CPI" in e.text for e in events)
    assert not any("Dec 8-9" in e.text for e in events)          # outside the window


def test_flags_name_held_stocks_with_earnings_and_market_wide_days():
    events = wa.events(DAYS, econ={date(2026, 10, 14): ("CPI (Sep)",)},
                       earnings={"TXN": Earnings(symbol="TXN", on=date(2026, 10, 21))},
                       fixed=())
    flags = wa.flags(events, held={"TXN": "picks", "MPC": "gemma"})
    assert any("TXN" in f and "picks" in f and "earnings" in f for f in flags)
    assert any("CPI" in f and "every position" in f for f in flags)
    assert not any("MPC" in f for f in flags)


def test_stock_lines_give_each_name_only_its_own_items():
    events = wa.events(DAYS, econ={}, fixed=(),
                       earnings={"TXN": Earnings(symbol="TXN", on=date(2026, 10, 21), when="after-close")})
    impacts = (ni.Impact(headline="Oil jumps on Gulf tanker attacks", symbols=("XOM", "MPC"),
                         sectors=("Energy",), effect="helps", why="oil", quote="Oil jumps"),)
    assert wa.stock_lines("TXN", events, impacts) == ["calendar: reports earnings Wed 10/21 after-close"]
    assert wa.stock_lines("MPC", events, impacts) == ["in the news: Oil jumps on Gulf tanker attacks"]
    assert wa.stock_lines("AMD", events, impacts) == []


def test_market_text_is_short_and_labelled_context():
    events = wa.events(DAYS, econ={date(2026, 10, 14): ("CPI (Sep)",)}, earnings={}, fixed=())
    text = wa.market_text(events, president_lines=["Iran blockade 'will remain in full force'"],
                          headlines=["OpenAI revenue miss hits AI stocks"])
    assert "Wed 10/14: CPI (Sep)" in text and "Iran blockade" in text and "OpenAI" in text
    assert "already in the price" in text
    assert len(text) < 2500


# --- news -> stocks ---------------------------------------------------------

HEADLINES = {"H1": "Oil jumps on Gulf tanker attacks", "H2": "OpenAI revenue miss hits AI stocks"}
UNIVERSE = ("XOM", "MPC", "AMD", "NVDA")
SECTORS = ("Energy", "Information Technology")


def test_impact_schema_is_names_only():
    s = ni.schema(HEADLINES, UNIVERSE, SECTORS)
    item = s["properties"]["links"]["items"]["properties"]
    assert item["headline"]["enum"] == ["H1", "H2"]
    assert item["symbols"]["items"]["enum"] == list(UNIVERSE)
    assert item["effect"]["enum"] == list(ni.EFFECTS)
    assert "number" not in str(s) and "integer" not in str(s)


def test_impact_validation_needs_a_real_quote_and_known_names():
    answer = {"links": [
        {"headline": "H1", "symbols": ["XOM", "MPC", "ZZZ"], "sectors": ["Energy"],
         "effect": "helps", "why": "oil up", "quote": "Gulf tanker attacks"},
        {"headline": "H2", "symbols": ["AMD"], "sectors": [], "effect": "hurts",
         "why": "AI demand", "quote": "OpenAI revenue beat"},             # not in the headline
        {"headline": "H9", "symbols": ["NVDA"], "sectors": [], "effect": "hurts",
         "why": "x", "quote": "anything at all"},                         # no such headline
    ]}
    out = ni.validate(answer, HEADLINES, UNIVERSE, SECTORS)
    assert len(out) == 1
    assert out[0].symbols == ("XOM", "MPC") and out[0].headline == HEADLINES["H1"]


def test_a_headline_linked_to_too_many_stocks_is_market_news():
    # 2026-10-19: "No 'Black Monday'? ... Retail Stays Cautious" was linked to 19
    # stocks and printed in each one's block, crowding out their own news.
    many = tuple(f"S{i}" for i in range(ni.MAX_LINK_SYMBOLS + 1))
    answer = {"links": [
        {"headline": "H1", "symbols": list(many), "sectors": [], "effect": "helps",
         "why": "market", "quote": "Oil jumps"},
        {"headline": "H2", "symbols": list(many[:ni.MAX_LINK_SYMBOLS]), "sectors": [],
         "effect": "hurts", "why": "AI", "quote": "OpenAI revenue miss"},
    ]}
    out = ni.validate(answer, HEADLINES, many, SECTORS)
    assert out[0].symbols == () and out[0].headline == HEADLINES["H1"]
    assert out[1].symbols == many[:ni.MAX_LINK_SYMBOLS]


def test_impact_validation_never_raises_on_garbage():
    assert ni.validate(None, HEADLINES, UNIVERSE, SECTORS) == ()
    assert ni.validate({"links": "x"}, HEADLINES, UNIVERSE, SECTORS) == ()


# --- president --------------------------------------------------------------

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>We will not be attacking Iran at any time prior to the Midterm Elections.</title>
<pubDate>Thu, 08 Oct 2026 16:17:11 +0000</pubDate><link>https://x/1</link></item>
<item><title>[No Title] - Post from October 8, 2026</title>
<pubDate>Thu, 08 Oct 2026 12:29:08 +0000</pubDate><link>https://x/2</link></item>
<item><title>Old post</title><pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_president_rss_keeps_recent_text_posts_in_full():
    now = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
    posts = pres.parse_rss(RSS, source="truth", now=now, hours=24)
    assert len(posts) == 1
    assert posts[0].text.endswith("prior to the Midterm Elections.")      # never truncated


def test_president_rss_rejects_entity_bombs():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss>&a;</rss>'
    assert pres.parse_rss(bomb, source="truth", now=datetime.now(timezone.utc)) == ()


def test_schedule_keeps_the_official_schedule_for_the_days_asked():
    rows = [{"date": "2026-10-09", "time_formatted": "11:00 AM", "type": "President Schedule",
             "details": "The President signs an Executive Order"},
            {"date": "2026-10-09", "time_formatted": "11:48 AM", "type": "Pool Report Schedule",
             "details": "Lunch lid"},
            {"date": "2026-10-01", "time_formatted": "", "type": "President Schedule",
             "details": "old"}]
    out = pres.parse_schedule(rows, days={date(2026, 10, 9)})
    assert [s.details for s in out] == ["The President signs an Executive Order"]


def test_schedule_drops_travel_and_pool_logistics():
    rows = [{"date": "2026-10-09", "time_formatted": None, "type": "President Schedule",
             "details": "TBD: The President departs the White House en route Camp David"},
            {"date": "2026-10-09", "time_formatted": "8:00 AM", "type": "President Schedule",
             "details": "The President participates in Executive Time"},
            {"date": "2026-10-09", "time_formatted": "3:00 PM", "type": "President Schedule",
             "details": "The President participates in a Policy Meeting"}]
    out = pres.parse_schedule(rows, days={date(2026, 10, 9)})
    assert [s.details for s in out] == ["The President participates in a Policy Meeting"]


def test_a_stock_gets_at_most_three_news_lines():
    impacts = tuple(ni.Impact(headline=f"MPC story {k}", symbols=("MPC",), sectors=(),
                              effect="unclear", why="", quote="MPC story") for k in range(5))
    assert len(wa.stock_lines("MPC", [], impacts)) == 3


# --- the wider calendar (§54) -----------------------------------------------

def test_options_expiry_is_the_third_friday_and_quad_witching_in_quarter_months():
    exp = dict(((d, t) for d, k, t in wa.market_mechanics(date(2026, 10, 1), date(2026, 12, 31))))
    assert "monthly options expiry" in exp[date(2026, 10, 16)]
    assert "monthly options expiry" in exp[date(2026, 11, 20)]
    assert "quad witching" in exp[date(2026, 12, 18)] and "S&P" in exp[date(2026, 12, 18)]


def test_holidays_and_early_closes_come_from_the_broker_sessions():
    from datetime import time as t
    from scanner.sources.market_calendar import Session
    sessions = (Session(on=date(2026, 11, 25), opens=t(9, 30), closes=t(16)),
                Session(on=date(2026, 11, 27), opens=t(9, 30), closes=t(13)))
    out = wa.closures(sessions, date(2026, 11, 25), date(2026, 11, 27))
    assert (date(2026, 11, 26), "market", "market CLOSED (holiday)") in out
    assert (date(2026, 11, 27), "market", "early close 13:00 ET") in out


def test_sector_events_touch_only_held_names_in_that_sector():
    evts = wa.events(DAYS, econ={}, earnings={}, fixed=(),
                     sector_econ={date(2026, 10, 13): (("OPEC Monthly Report", ("Energy",)),)})
    flags = wa.flags(evts, held={"MPC": "gemma", "AMD": "picks"},
                     sectors={"MPC": "Energy", "AMD": "Information Technology"})
    assert any("OPEC" in f and "MPC" in f for f in flags)
    assert not any("OPEC" in f and "AMD" in f for f in flags)
    assert wa.stock_lines("MPC", evts, (), sector="Energy") == ["calendar: Tue 10/13 OPEC Monthly Report (Energy)"]


def test_bellwether_earnings_reach_the_whole_sector():
    evts = wa.events(DAYS, econ={}, fixed=(), earnings={},
                     bellwethers={"TSM": Earnings(symbol="TSM", on=date(2026, 10, 15), when="pre-mkt")},
                     bellwether_sectors={"TSM": ("Information Technology",)})
    lines = wa.stock_lines("AMD", evts, (), sector="Information Technology")
    assert lines == ["calendar: Thu 10/15 TSM reports earnings pre-mkt (bellwether for Information Technology)"]


def test_economic_parse_splits_market_wide_from_sector_events():
    from scanner.sources.calendar import parse_sector_events, parse_economic
    rows = [{"country": "United States", "eventName": n} for n in
            ("Retail Sales", "Beige Book", "OPEC Monthly Report", "Crude Oil Inventories",
             "Housing Starts", "30-Year Bond Auction", "Redbook")]
    payload = {"data": {"rows": rows}}
    assert set(parse_economic(payload)) == {"Retail Sales", "Beige Book", "30-Year Bond Auction"}
    assert dict(parse_sector_events(payload)) == {
        "OPEC Monthly Report": ("Energy",), "Crude Oil Inventories": ("Energy",),
        "Housing Starts": ("Real Estate", "Consumer Discretionary")}


# --- expected vs actual (§56) -----------------------------------------------

def test_releases_keep_actual_expected_and_previous_joining_repeat_rows():
    from scanner.sources.calendar import parse_releases
    rows = [{"country": "United States", "eventName": "CPI", "actual": "&nbsp;", "consensus": " ",
             "previous": "0.4%"},
            {"country": "United States", "eventName": "CPI", "actual": "&nbsp;", "consensus": "3.3%",
             "previous": "3.4%"},
            {"country": "United States", "eventName": "Crude Oil Inventories", "actual": "-3.186M",
             "consensus": "1.900M", "previous": "0.922M"},
            {"country": "Germany", "eventName": "German CPI", "actual": "2%", "consensus": "2%",
             "previous": "2%"}]
    out = {r.name: r for r in parse_releases({"data": {"rows": rows}})}
    assert set(out) == {"CPI", "Crude Oil Inventories"}
    assert out["CPI"].actual == "" and out["CPI"].previous == "0.4% / 3.4%"
    assert out["CPI"].consensus == "3.3%"
    assert out["Crude Oil Inventories"].actual == "-3.186M"


def test_surprise_reads_numbers_with_units():
    from scanner.sources.calendar import surprise
    assert surprise("-3.186M", "1.900M") == "below"
    assert surprise("197K", "200K") == "below"
    assert surprise("0.5%", "0.3%") == "above"
    assert surprise("1,716K", "1,710K") == "above"
    assert surprise("0.3%", "0.3%") == "in line"
    assert surprise("", "0.3%") == "" and surprise("0.3%", "") == ""


def test_market_text_shows_what_is_expected_and_what_came_in():
    from scanner.sources.calendar import Release
    evts = wa.events(DAYS, econ={date(2026, 10, 14): (Release(name="CPI", consensus="0.3%",
                                                              previous="0.4%"),)},
                     earnings={}, fixed=())
    results = (Release(name="Initial Jobless Claims", actual="197K", consensus="200K"),)
    text = wa.market_text(evts, president_lines=[], headlines=[], results=results)
    assert "Wed 10/14: CPI (expected 0.3%, last 0.4%)" in text
    assert "Initial Jobless Claims 197K vs 200K expected (below)" in text


# --- big money movers (§58) -------------------------------------------------

def test_global_central_banks_are_kept_with_their_country():
    from scanner.sources.calendar import parse_global
    rows = [{"country": "Euro Zone", "eventName": "ECB Interest Rate Decision", "previous": "2.00%"},
            {"country": "Japan", "eventName": "BoJ Interest Rate Decision"},
            {"country": "Euro Zone", "eventName": "Deposit Facility Rate"},
            {"country": "Germany", "eventName": "German CPI"},
            {"country": "Russia", "eventName": "Interest Rate Decision"}]
    names = [r.name for r in parse_global({"data": {"rows": rows}})]
    assert names == ["Euro Zone: ECB Interest Rate Decision", "Japan: BoJ Interest Rate Decision"]


def test_only_big_ipos_make_the_calendar():
    from scanner.sources.calendar import parse_ipos
    payload = {"data": {"upcoming": {"upcomingTable": {"rows": [
        {"proposedTickerSymbol": "BIG", "companyName": "Big Co", "expectedPriceDate": "10/15/2026",
         "dollarValueOfSharesOffered": "$2,400,000,000"},
        {"proposedTickerSymbol": "SML", "companyName": "Small Co", "expectedPriceDate": "10/15/2026",
         "dollarValueOfSharesOffered": "$150,000,000"}]}}}}
    out = parse_ipos(payload, min_dollars=1e9)
    assert out == ((date(2026, 10, 15), "IPO: Big Co (BIG), about $2.4B raised"),)


def test_fixed_sector_events_reach_their_sector():
    evts = wa.events([date(2026, 11, 17)], econ={}, earnings={}, fixed=(),
                     fixed_sector=((date(2026, 11, 17), "Microsoft Ignite (Nov 17-20)",
                                    ("Information Technology",)),))
    assert wa.stock_lines("AMD", evts, (), sector="Information Technology") == \
        ["calendar: Tue 11/17 Microsoft Ignite (Nov 17-20) (Information Technology)"]


# --- market dashboard (§61) -------------------------------------------------

def test_dashboard_rows_show_changes_and_sort_sectors_by_month():
    closes = {"^GSPC": [100.0] * 20 + [101.0, 102.0], "XLE": [50.0] * 21 + [55.0],
              "XLK": [50.0] * 21 + [45.0]}
    groups = (("US indexes", (("^GSPC", "S&P 500"),)),
              ("Sectors", (("XLK", "Technology"), ("XLE", "Energy"))))
    md = wa.dashboard_md(groups, closes)
    assert "| S&P 500 | +0.99% |" in md
    assert md.index("Energy") < md.index("Technology")      # sectors: best month first
    assert "| Energy | +10.00% | +10.00% | +10.00% |" in md


def test_dashboard_skips_missing_symbols():
    md = wa.dashboard_md((("US indexes", (("^GSPC", "S&P 500"), ("^XYZ", "Missing"))),),
                         {"^GSPC": [100.0] * 25})
    assert "S&P 500" in md and "Missing" not in md
