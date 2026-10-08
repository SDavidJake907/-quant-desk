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
