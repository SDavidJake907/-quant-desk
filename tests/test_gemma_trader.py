"""Gemma's book: it may only name stocks, every new buy needs evidence, and a
bad or missing answer changes nothing."""

from __future__ import annotations

import pytest

from scanner.advisor import gemma_trader as gt

FACTS = {
    "NKE": "NKE | 1m -8.0% | 3m -15.0% | 12m -44.0% | 12-1 -44.9%\n"
           "headline: Nike shares slide after analyst downgrade on China weakness",
    "GS": "GS | 1m +6.0% | 3m +12.0% | 12m +40.0% | 12-1 +38.0%\n"
          "headline: Goldman Sachs trading revenue beats estimates on market volatility",
    "ANET": "ANET | 1m +3.0% | 3m +9.0% | 12m +60.0% | 12-1 +58.0%\n"
            "headline: Arista wins hyperscaler AI networking order",
}


def test_schema_limits_symbols_to_the_allowed_list_and_count():
    schema = gt.schema(("GS", "ANET"), 5)
    item = schema["properties"]["holdings"]["items"]
    assert item["properties"]["symbol"]["enum"] == ["GS", "ANET"]
    assert schema["properties"]["holdings"]["maxItems"] == 5
    assert "number" not in str(schema) and "integer" not in str(schema)


def test_prompt_states_the_charter_the_rules_and_the_facts():
    text = gt.prompt(FACTS, held={"GS": "+2.1% since entry"}, max_positions=5,
                     scoreboard="book +1.2% vs S&P +0.4% vs momentum +0.9%")
    assert "beaten BOTH the S&P 500" in text and "momentum rule" in text
    assert "2027-04-06" in text and "25%" in text
    assert "Goldman Sachs trading revenue beats" in text
    assert "GS" in text and "+2.1% since entry" in text
    assert "book +1.2% vs S&P +0.4% vs momentum +0.9%" in text


def test_prompt_asks_for_its_own_judgement_and_discourages_churn():
    text = gt.prompt(FACTS, held={}, max_positions=5, scoreboard="")
    assert "cannot beat" in text                      # copying momentum is not the job
    assert "costs" in text and "unless" in text       # keep a holding unless its reason changed
    assert "no scoreboard yet" in text.lower()


def test_grounded_new_buys_kept_ungrounded_dropped():
    answer = {"holdings": [
        {"symbol": "GS", "reason": "leader", "evidence": "trading revenue beats estimates"},
        {"symbol": "ANET", "reason": "AI demand", "evidence": "Arista is the best stock ever"},
    ], "journal": "x"}
    d = gt.validate(answer, FACTS, held=set(), max_positions=5)
    assert d.keep == ("GS",)
    assert [s for s, _ in d.rejected] == ["ANET"]


def test_an_ungrounded_call_on_a_held_name_keeps_it_rather_than_churning():
    answer = {"holdings": [{"symbol": "NKE", "reason": "hold", "evidence": "made up"}]}
    d = gt.validate(answer, FACTS, held={"NKE"}, max_positions=5)
    assert d.keep == ("NKE",)


def test_unknown_symbols_duplicates_and_extras_are_cut():
    answer = {"holdings": [
        {"symbol": "GS", "reason": "a", "evidence": "trading revenue beats estimates"},
        {"symbol": "GS", "reason": "a", "evidence": "trading revenue beats estimates"},
        {"symbol": "TSLA", "reason": "b", "evidence": "anything"},
        {"symbol": "ANET", "reason": "c", "evidence": "wins hyperscaler AI networking order"},
    ]}
    d = gt.validate(answer, FACTS, held=set(), max_positions=1)
    assert d.keep == ("GS",)
    reasons = dict(d.rejected)
    assert "not on the list" in reasons["TSLA"] and "over the limit" in reasons["ANET"]


def test_too_short_a_quote_is_not_evidence():
    answer = {"holdings": [{"symbol": "GS", "reason": "a", "evidence": "GS"}]}
    assert gt.validate(answer, FACTS, held=set(), max_positions=5).keep == ()


@pytest.mark.parametrize("answer", [None, {}, {"holdings": "GS"}, {"holdings": [7]}])
def test_a_malformed_answer_keeps_the_book_unchanged(answer):
    d = gt.validate(answer, FACTS, held={"NKE", "GS"}, max_positions=5)
    assert d.keep == ("GS", "NKE") and d.unchanged


def test_diff_buys_new_and_sells_dropped():
    assert gt.diff(("GS", "ANET"), {"GS", "NKE"}) == (("ANET",), ("NKE",))
