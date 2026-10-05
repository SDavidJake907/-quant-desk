"""Marking the model's live calls against what the trades then did.

The model makes two kinds of call that are recorded but never enforced: a
conviction on each candidate at entry, and a hold/exit review of each open
position every scan. Neither has been scored. Seventy-odd reviews in, every
one says "hold", and nothing in the repo could say whether "strong" means
anything. These pin down how each call is joined to its trade.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.llm.scorecard import (
    entry_epoch,
    parse_review,
    score,
)

UTC = timezone.utc


def _at(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def outcome(symbol, epoch, r, *, exit_reason="stop", policy="p1", at=None):
    return {"event": "trade_outcome", "symbol": symbol, "policy": policy,
            "at": _at(at or epoch + 86_400),
            "outcome": {"symbol": symbol, "realized_r": r,
                        "exit_reason": exit_reason,
                        "client_order_id": f"smc-{symbol}-{epoch}"}}


def review(symbol, epoch, call="hold", confidence="strong", policy="p1"):
    return {"event": "position_review", "symbol": symbol, "policy": policy,
            "at": _at(epoch),
            "reason": f"{call} ({confidence}, grounded): drifting."}


def slate(epoch, *items, policy="p1"):
    return {"event": "candidate_slate", "policy": policy, "at": _at(epoch + 60),
            "slate": [{"symbol": s, "llm_conviction": c,
                       "trigger_at": _at(epoch)} for s, c in items]}


T0 = 1_790_789_400


class TestParsing:
    def test_entry_epoch_comes_from_the_client_order_id(self):
        assert entry_epoch("smc-SNPS-1790789400") == 1790789400

    def test_a_manual_or_foreign_order_id_has_no_epoch(self):
        assert entry_epoch("manual-123") is None
        assert entry_epoch("") is None

    def test_review_reason_is_parsed(self):
        assert parse_review("hold (strong, grounded): x") == ("hold", "strong", True)
        assert parse_review("exit (neutral, ungrounded): x") == (
            "exit", "neutral", False)

    def test_an_unparseable_review_is_none_not_a_guess(self):
        assert parse_review("model unavailable") is None


class TestReviews:
    def test_a_review_belongs_to_the_trade_open_when_it_was_made(self):
        rows = [outcome("MLM", T0, -1.0), review("MLM", T0 + 3600)]
        card = score(rows)
        assert card.reviews_total == 1
        assert [(t.symbol, t.reviews) for t in card.reviewed_trades] == [("MLM", 1)]

    def test_a_review_before_entry_is_not_attributed(self):
        rows = [outcome("MLM", T0, -1.0), review("MLM", T0 - 3600)]
        assert score(rows).reviewed_trades == ()

    def test_a_review_of_a_still_open_position_is_pending(self):
        card = score([review("INTC", T0)])
        assert card.reviews_pending == 1
        assert card.reviewed_trades == ()

    def test_a_symbol_traded_twice_splits_its_reviews_correctly(self):
        later = T0 + 10 * 86_400
        rows = [outcome("AON", T0, -1.0, at=T0 + 2 * 86_400),
                outcome("AON", later, 2.0, exit_reason="target",
                        at=later + 86_400),
                review("AON", T0 + 600, confidence="strong"),
                review("AON", later + 600, confidence="neutral")]
        trades = {t.realized_r: t.last_confidence for t in score(rows).reviewed_trades}
        assert trades == {-1.0: "strong", 2.0: "neutral"}

    def test_exit_calls_are_counted(self):
        rows = [review("A", T0, call="hold"), review("A", T0 + 1, call="exit")]
        card = score(rows)
        assert card.exit_calls == 1 and card.reviews_total == 2

    def test_mean_r_is_grouped_by_the_last_confidence_before_exit(self):
        rows = [outcome("A", T0, -1.0), review("A", T0 + 60, confidence="neutral"),
                review("A", T0 + 120, confidence="strong"),
                outcome("B", T0, 3.0, exit_reason="target"),
                review("B", T0 + 60, confidence="strong")]
        groups = {g.label: g for g in score(rows).review_groups}
        assert groups["strong"].n == 2
        assert groups["strong"].mean_r == 1.0


def submitted(symbol, epoch, at, conviction="", policy="p1"):
    return {"event": "order_submitted", "symbol": symbol, "policy": policy,
            "at": _at(at),
            "intent": {"client_order_id": f"smc-{symbol}-{epoch}",
                       "llm_conviction": conviction}}


class TestReentry:
    def test_a_reentry_before_the_outcome_is_recorded_does_not_steal_reviews(self):
        """Trade A exits Monday, its outcome is recorded Tuesday evening. Trade
        B in the same name opens Monday afternoon and is still open. B's
        reviews fall inside A's [entry, recorded] window and must not be
        scored as A's."""
        a, b = T0, T0 + 20 * 3600
        rows = [submitted("INTC", a, a + 1980),
                outcome("INTC", a, -1.0, at=a + 40 * 3600),
                submitted("INTC", b, b + 1980),
                review("INTC", a + 3600),
                review("INTC", b + 3600)]
        card = score(rows)
        assert [(t.symbol, t.reviews) for t in card.reviewed_trades] == [("INTC", 1)]
        assert card.reviews_pending == 1

    def test_a_duplicated_outcome_row_counts_once(self):
        rows = [outcome("A", T0, -1.0), outcome("A", T0, -1.0),
                review("A", T0 + 60)]
        assert len(score(rows).reviewed_trades) == 1


class TestEntryConviction:
    def test_conviction_comes_from_the_order_that_was_actually_sent(self):
        """The slate is re-ranked every scan; the order records what the model
        said at the moment of entry."""
        rows = [slate(T0, ("TSCO", "weak")),
                submitted("TSCO", T0, T0 + 1980, conviction="strong"),
                outcome("TSCO", T0, 1.5)]
        groups = {g.label: g for g in score(rows).conviction_groups}
        assert set(groups) == {"strong"}

    def test_conviction_is_joined_on_symbol_and_trigger_bar(self):
        rows = [slate(T0, ("TSCO", "strong"), ("LULU", "neutral")),
                outcome("TSCO", T0, 1.5, exit_reason="target")]
        groups = {g.label: g for g in score(rows).conviction_groups}
        assert groups["strong"].n == 1 and groups["strong"].mean_r == 1.5
        assert "neutral" not in groups   # LULU never filled

    def test_the_same_slate_repeated_across_scans_counts_once(self):
        rows = [slate(T0, ("TSCO", "strong")), slate(T0, ("TSCO", "strong")),
                outcome("TSCO", T0, 1.5)]
        groups = {g.label: g for g in score(rows).conviction_groups}
        assert groups["strong"].n == 1

    def test_unranked_candidates_are_labelled_not_dropped(self):
        rows = [slate(T0, ("INTC", "")), outcome("INTC", T0, -0.5)]
        groups = {g.label: g for g in score(rows).conviction_groups}
        assert groups["unranked"].n == 1


class TestPolicyFilter:
    def test_filtering_by_policy_excludes_other_windows(self):
        rows = [outcome("A", T0, -1.0, policy="old"),
                review("A", T0 + 60, policy="old"),
                outcome("B", T0, 2.0, policy="new"),
                review("B", T0 + 60, policy="new")]
        card = score(rows, policy="new")
        assert [t.symbol for t in card.reviewed_trades] == ["B"]

    def test_a_trade_is_filtered_by_its_own_policy_not_its_reviews(self):
        """MLM's outcome carries the policy it was entered under; reviews
        written after a policy change carry the new one. Filtering rows
        independently would orphan the reviews and drop the trade."""
        rows = [outcome("MLM", T0, -1.0, policy="old"),
                review("MLM", T0 + 60, policy="new")]
        assert [t.symbol for t in score(rows, policy="old").reviewed_trades] == ["MLM"]
        new = score(rows, policy="new")
        assert new.reviewed_trades == () and new.reviews_pending == 0


class TestGate:
    def test_news_refusals_are_listed_but_capacity_refusals_are_not(self):
        rows = [{"event": "candidate_refused", "stage": "confirm", "symbol": "GEN",
                 "at": _at(T0), "policy": "p1",
                 "reason": "being acquired - price tracks the deal"},
                {"event": "candidate_refused", "stage": "confirm", "symbol": "LITE",
                 "at": _at(T0), "policy": "p1",
                 "reason": "cannot afford one share"},
                {"event": "candidate_refused", "stage": "confirm", "symbol": "NKE",
                 "at": _at(T0), "policy": "p1",
                 "reason": "reports earnings Thu 10/01 after-close"},
                {"event": "candidate_refused", "stage": "confirm", "symbol": "AMD",
                 "at": _at(T0), "policy": "p1", "reason": "structural news (mna)"},
                {"event": "candidate_refused", "stage": "watch", "symbol": "INTC",
                 "at": _at(T0), "policy": "p1", "reason": "already holding"}]
        """Earnings come from the calendar and capacity from sizing; only the
        deal-role and structural-category refusals are the model's."""
        assert [s for s, _, _ in score(rows).gate_refusals] == ["GEN", "AMD"]

    def test_a_refusal_repeated_every_scan_is_listed_once(self):
        row = {"event": "candidate_refused", "stage": "confirm", "symbol": "GEN",
               "at": _at(T0), "policy": "p1",
               "reason": "being acquired - price tracks the deal"}
        later = {**row, "at": _at(T0 + 1800)}
        assert len(score([row, later]).gate_refusals) == 1

    def test_degraded_scans_are_counted(self):
        rows = [{"event": "news_gate_degraded", "at": _at(T0), "policy": "p1"}] * 3
        assert score(rows).gate_degraded == 3


def ta(symbol, epoch, rating="Buy"):
    return {"event": "tradingagents_rating", "symbol": symbol, "policy": "p1",
            "at": _at(epoch), "reason": f"{rating}; trader=Buy; mode=premarket; secs=120; report=x"}


class TestTradingAgents:
    """TradingAgents' rating, joined to the trade it could have filtered."""

    def test_a_rating_before_entry_groups_that_trade(self):
        rows = [ta("SNPS", T0 - 3_600, "Buy"), outcome("SNPS", T0, 2.0),
                ta("MLM", T0 - 7_200, "Sell"), outcome("MLM", T0, -1.0)]
        card = score(rows)
        groups = {g.label: (g.n, g.mean_r) for g in card.ta_entry_groups}
        assert groups == {"Buy": (1, 2.0), "Sell": (1, -1.0)}

    def test_the_latest_rating_before_entry_wins(self):
        rows = [ta("SNPS", T0 - 80_000, "Sell"), ta("SNPS", T0 - 600, "Overweight"),
                outcome("SNPS", T0, 1.0)]
        assert [g.label for g in score(rows).ta_entry_groups] == ["Overweight"]

    def test_a_rating_too_old_to_have_informed_the_entry_is_ignored(self):
        rows = [ta("SNPS", T0 - 5 * 86_400, "Buy"), outcome("SNPS", T0, 1.0)]
        assert score(rows).ta_entry_groups == ()

    def test_a_rating_made_while_the_trade_was_open_is_scored_apart(self):
        rows = [outcome("SNPS", T0, -1.0), ta("SNPS", T0 + 3_600, "Sell")]
        card = score(rows)
        assert card.ta_entry_groups == ()
        assert [(g.label, g.n) for g in card.ta_during_groups] == [("Sell", 1)]

    def test_ratings_and_failures_are_counted(self):
        rows = [ta("SNPS", T0), ta("MLM", T0),
                {"event": "tradingagents_error", "symbol": "ZZZ", "policy": "p1",
                 "at": _at(T0), "reason": "failed: x"}]
        card = score(rows)
        assert (card.ta_ratings, card.ta_errors) == (2, 1)
