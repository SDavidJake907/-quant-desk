"""Gemma's portfolio review and Ask Gemma (operator 2026-10-19).

The numbers are computed here, never written by Gemma; her answer is checked
like every other answer of hers: held names only for verdicts, candidates only
for the watchlist, and an unusable answer still yields a review of the book.
"""

from scanner.advisor import gemma_review as rv

POSITIONS = [
    {"symbol": "MU", "qty": "8", "avg_entry_price": "873.43", "current_price": "907.74",
     "market_value": "7261.92", "unrealized_pl": "274.48", "unrealized_plpc": "0.0393"},
    {"symbol": "VLO", "qty": "9", "avg_entry_price": "423.46", "current_price": "440.17",
     "market_value": "3961.53", "unrealized_pl": "150.39", "unrealized_plpc": "0.0395"},
]


def test_position_views_compute_weight_and_room_to_the_stop():
    views = rv.position_views(POSITIONS, {"MU": 761.81}, sleeve=50_000)
    mu, vlo = views
    assert mu.symbol == "MU" and round(mu.pl_pct, 1) == 3.9
    assert round(mu.weight_pct, 1) == 14.5
    assert round(mu.stop_gap_pct, 1) == round((907.74 - 761.81) / 907.74 * 100, 1)
    assert vlo.stop is None and vlo.stop_gap_pct is None


def test_book_numbers_show_money_at_work_and_cash():
    views = rv.position_views(POSITIONS, {}, sleeve=50_000)
    n = rv.book_numbers(views, sleeve=50_000)
    assert round(n["invested"]) == 11223
    assert round(n["invested_pct"], 1) == 22.4
    assert round(n["idle"]) == 50_000 - 11223
    assert n["positions"] == 2


def test_prompt_carries_her_book_and_candidates_and_asks_for_a_review():
    views = rv.position_views(POSITIONS, {"MU": 761.81}, sleeve=50_000)
    text = rv.prompt({"MU": "[MU facts]", "VLO": "[VLO facts]", "DELL": "[DELL facts]"},
                     views, numbers=rv.book_numbers(views, sleeve=50_000),
                     scoreboard="SCORE", market="MARKET", candidates=["DELL"])
    assert "[DELL facts]" in text and "[MU facts]" in text
    assert "SCORE" in text and "MARKET" in text
    assert "review" in text.lower() and "watchlist" in text.lower()
    assert "22%" in text


def test_schema_limits_verdicts_and_names():
    s = rv.schema(["MU", "VLO"], ["DELL", "STX"])
    pos = s["properties"]["positions"]["items"]["properties"]
    assert pos["symbol"]["enum"] == ["MU", "VLO"]
    assert pos["verdict"]["enum"] == list(rv.VERDICTS)
    assert s["properties"]["watchlist"]["items"]["properties"]["symbol"]["enum"] == ["DELL", "STX"]
    assert s["properties"]["watchlist"]["maxItems"] == rv.WATCHLIST


def test_validate_keeps_only_allowed_names_and_fills_missing_holdings():
    answer = {
        "summary": "fine",
        "positions": [{"symbol": "MU", "verdict": "keep", "reason": "trend intact"},
                      {"symbol": "TSLA", "verdict": "keep", "reason": "not mine"},
                      {"symbol": "MU", "verdict": "watch", "reason": "duplicate"}],
        "watchlist": [{"symbol": "DELL", "why": "AI servers", "buy_if": "holds 140"},
                      {"symbol": "MU", "why": "held", "buy_if": "x"},
                      {"symbol": "DELL", "why": "again", "buy_if": "x"}],
        "upcoming": ["VLO earnings Wed"],
        "proposals": [{"change": "raise size", "why": "43% invested"}] * 5,
    }
    out = rv.validate(answer, held=["MU", "VLO"], candidates=["DELL"])
    assert [p["symbol"] for p in out["positions"]] == ["MU", "VLO"]
    assert out["positions"][0]["reason"] == "trend intact"
    assert out["positions"][1]["verdict"] == "keep" and "no review" in out["positions"][1]["reason"]
    assert [w["symbol"] for w in out["watchlist"]] == ["DELL"]
    assert len(out["proposals"]) == rv.PROPOSALS


def test_an_unusable_answer_is_still_a_review_of_the_book():
    out = rv.validate(None, held=["MU"], candidates=["DELL"])
    assert [p["symbol"] for p in out["positions"]] == ["MU"]
    assert out["watchlist"] == [] and out["proposals"] == []
    assert "unavailable" in out["summary"]


def test_review_text_is_compact_context_for_questions():
    review = {"day": "2026-10-19", "summary": "Holding five.",
              "numbers": {"invested": 21520, "invested_pct": 43.0, "idle": 29000, "positions": 5},
              "positions": [{"symbol": "MU", "verdict": "keep", "reason": "trend", "pl_pct": 3.9}],
              "watchlist": [{"symbol": "DELL", "why": "AI", "buy_if": "holds"}],
              "proposals": [{"change": "raise size", "why": "cash"}]}
    text = rv.review_text(review)
    assert "Holding five." in text and "MU: keep" in text and "DELL" in text
    assert "raise size" in text and "43%" in text
    assert rv.review_text(None) == "(no review yet)"


def test_ask_prompt_has_the_question_context_and_history():
    text = rv.ask_prompt("Why VLO?", briefing="BRIEF", review="REVIEW",
                         history=[("Earlier q", "Earlier a")])
    assert "Why VLO?" in text and "BRIEF" in text and "REVIEW" in text
    assert "Earlier q" in text and "Earlier a" in text
    assert text.rstrip().endswith("Why VLO?") or "Question:" in text


def test_clean_question_trims_and_refuses_empty_or_long():
    assert rv.clean_question("  why VLO?  ") == "why VLO?"
    assert rv.clean_question("   ") is None
    assert rv.clean_question(None) is None
    assert rv.clean_question("x" * (rv.MAX_QUESTION_CHARS + 1)) is None


def test_her_sell_if_is_shown_and_she_says_whether_it_is_met():
    views = rv.position_views(POSITIONS, {}, sleeve=50_000, sell_ifs={"MU": "guidance cut"})
    text = rv.prompt({"MU": "x", "VLO": "y"}, views, numbers=rv.book_numbers(views, sleeve=50_000),
                     scoreboard="", market="", candidates=[])
    assert "your sell-if: guidance cut" in text and "no sell-if given yet" in text
    s = rv.schema(["MU", "VLO"], [])
    assert s["properties"]["positions"]["items"]["properties"]["sell_if_met"]["enum"] == \
        list(rv.SELL_IF_MET)
    out = rv.validate({"summary": "s", "positions": [
        {"symbol": "MU", "verdict": "keep", "reason": "r", "sell_if_met": "yes"},
        {"symbol": "VLO", "verdict": "keep", "reason": "r", "sell_if_met": "maybe"}]},
        held=["MU", "VLO"], candidates=[])
    assert [p["sell_if_met"] for p in out["positions"]] == ["yes", "unclear"]
