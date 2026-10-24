"""Gemma's web tools (advisor/web_tools.py, operator + Gemma 2026-10-25)."""

import asyncio

from scanner.advisor import stock_tools
from scanner.advisor import web_tools as wt
from scanner.ops import market_monitor as mm

DDG = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fokta&amp;rut=x">'
       'Okta <b>beats</b></a><a class="result__snippet" href="x">Okta raised &amp; guidance</a>'
       '<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>'
       '<a class="result__a" href="https://site.com/b">Second</a>')


def test_parse_results_unwraps_links_and_skips_ads():
    rows = wt.parse_results(DDG)
    assert rows[0] == ("Okta beats", "https://example.com/okta", "Okta raised & guidance")
    assert [r[1] for r in rows] == ["https://example.com/okta", "https://site.com/b"]


def test_page_text_drops_scripts_and_menus():
    page = ("<html><head><title>OKTA up</title><script>var x=1</script></head><body><nav>Menu</nav>"
            "<p>Okta   raised its outlook.</p><footer>c</footer></body></html>")
    assert wt.page_text(page) == "OKTA up -- Okta raised its outlook."


def test_private_and_local_addresses_are_refused():
    assert not wt.is_public("127.0.0.1") and not wt.is_public("192.168.1.5") and not wt.is_public("::1")
    assert wt.is_public("8.8.8.8")
    assert "only http(s)" in asyncio.run(wt._check("file:///c:/secrets.txt"))
    assert "not a public address" in asyncio.run(wt._check("http://localhost:11434/api/tags"))
    out = asyncio.run(stock_tools.execute("read_page", {"url": "http://127.0.0.1:8000/"}, client=None, broker=None))
    assert out.startswith("Refused")


def test_web_lookups_join_the_named_stocks_facts():
    calls = [{"tool": "web_search", "args": {"query": "MU guidance cut"}, "result": "Micron cuts guidance"},
             {"tool": "web_search", "args": {"query": "memory chip prices"}, "result": "prices fall"}]
    out = mm.with_lookups({"MU": "f", "MUSA": "g"}, calls)
    assert "Micron cuts guidance" in out["MU"] and out["MUSA"] == "g"


def test_web_tools_are_registered():
    names = {t["function"]["name"] for t in stock_tools.TOOLS}
    assert {"web_search", "read_page"} <= names == set(stock_tools.HANDLERS)
