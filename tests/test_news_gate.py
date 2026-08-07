"""News-gate tests.

The gate answers one question before an order goes out: does this name still
trade on its own chart? It never sets a price, and it never decides alone —
`blocking` is computed here from categories, in Python.
"""

from __future__ import annotations

from scanner.execution.news_gate import NewsFlag


def test_being_acquired_blocks_the_trade():
    """SUPN, 2026-08-06: textbook setup, and an announced all-stock merger.
    A target's price tracks the exchange ratio, not its own structure — and
    there is nothing to wait for, so it is refused rather than watch-listed."""
    flag = NewsFlag(symbol="SUPN", categories=("mna", "earnings"),
                    deal_role="target")
    assert flag.blocking
    assert flag.is_takeover_target
    assert not flag.verdict()
    assert "acquired" in flag.verdict().reason


def test_doing_the_acquiring_does_not_block():
    """AMD, 2026-08-06: 'AMD deepens AI inference bet with Taalas deal'. AMD
    is the buyer. Its chart still means what it meant yesterday, and blocking
    on the mna CATEGORY refused it — a false positive against exactly the
    large liquid names the universe study preferred."""
    flag = NewsFlag(symbol="AMD", categories=("mna", "earnings"),
                    deal_role="acquirer")
    assert not flag.blocking
    assert flag.verdict()


def test_a_deal_category_without_a_role_does_not_block():
    """The category alone cannot tell buyer from bought, so it must not be
    the thing that decides."""
    assert NewsFlag(symbol="X", categories=("mna",)).verdict()


def test_ordinary_news_does_not_block():
    flag = NewsFlag(symbol="MU", categories=("earnings", "analyst", "product"))
    assert not flag.blocking
    assert flag.verdict()


def test_litigation_is_structural_too():
    assert NewsFlag(symbol="X", categories=("legal",)).blocking


def test_no_news_is_not_a_block():
    assert NewsFlag(symbol="X").verdict()


def test_an_unchecked_symbol_is_allowed_but_says_so():
    """A news outage must not stop trading — but it must not masquerade as a
    clean bill of health either."""
    flag = NewsFlag(symbol="X", checked=False)
    assert flag.verdict()
    assert "not checked" in flag.verdict().reason


def test_categories_are_deduplicated_in_the_structural_list():
    flag = NewsFlag(symbol="X", categories=("legal", "legal", "earnings"))
    assert flag.structural == ("legal",)


# ---- earnings ----------------------------------------------------------

def test_a_company_reporting_soon_is_blocked():
    """Gapped stops are this strategy's fat tail and earnings are the most
    predictable gap there is. LITE reports 08/11 after close — and LITE is one
    of the five worst trades in the study, a -2.01R gapped stop."""
    flag = NewsFlag(symbol="LITE", earnings="Tue 08/11 after-close")
    assert flag.blocking
    assert not flag.verdict()
    assert "earnings" in flag.verdict().reason


def test_earnings_outrank_ordinary_news_in_the_reason():
    flag = NewsFlag(symbol="X", categories=("analyst",), earnings="Mon 08/10")
    assert "earnings" in flag.verdict().reason


def test_no_scheduled_earnings_does_not_block():
    assert NewsFlag(symbol="X", categories=("earnings",)).verdict()


def test_earnings_block_even_when_the_news_scan_failed():
    """A news outage must not become permission to hold through a report."""
    flag = NewsFlag(symbol="X", checked=False, earnings="Tue 08/11")
    assert flag.blocking
