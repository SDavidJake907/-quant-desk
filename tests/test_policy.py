"""Policy and decision-record tests.

The point of a policy object is that six months from now, a losing month can
be traced to the exact rules that produced it. That only works if the
fingerprint moves when a rule moves, and if refusals are recorded rather than
printed and forgotten.
"""

from __future__ import annotations

from scanner.execution.policy import (
    CONFIRM,
    SCAN,
    TRADE,
    WATCH,
    Decision,
    TradingPolicy,
    rejections,
    summarise,
)


def test_the_fingerprint_moves_when_a_rule_moves():
    """Otherwise two different rule sets could produce the same audit trail."""
    base = TradingPolicy()
    assert base.fingerprint == TradingPolicy().fingerprint
    assert base.fingerprint != TradingPolicy(min_risk_pct=2.0).fingerprint
    assert base.fingerprint != TradingPolicy(max_positions=3).fingerprint


def test_the_fingerprint_is_stable_across_field_order():
    a = TradingPolicy(min_risk_pct=1.5, max_positions=5)
    b = TradingPolicy(max_positions=5, min_risk_pct=1.5)
    assert a.fingerprint == b.fingerprint


def test_the_holdout_configuration_is_recorded_even_where_it_no_longer_holds():
    """The +0.218R out-of-sample result was measured on FOUR defaults. Three
    still hold. One does not, and that is written down rather than quietly
    re-pinned.

    `require_value_sweep` was True when +0.218R was measured and is now False.
    Measured against itself on the 30m Alpaca store, same scan, flag toggled:

        True   n=860   +0.054R  se 0.058   total +46.3R
        False  n=1468  +0.039R  se 0.044   total +57.1R

    The quality difference is +0.015R against standard errors near 0.05 --
    indistinguishable from zero -- while the filter discards 41% of the sample.

    SO +0.218R NO LONGER DESCRIBES THE LIVE CONFIGURATION. It was already
    weakened: it came from a 96-session Yahoo window that contained no
    down-trending sessions, the same window that produced +0.311R while two
    years of the same rules produce +0.000R, and it sits at a p-value that does
    not survive a Bonferroni correction at ~70 declared trials.

    This test now pins the three that still hold, and pins the fourth as
    CHANGED so nobody quotes +0.218R as though it described what runs.
    """
    p = TradingPolicy()
    assert p.interval_minutes == 30
    assert p.swing_k == 2
    assert p.min_risk_pct == 1.5
    assert p.require_value_sweep is False, (
        "if this is True again, the orphaning above no longer applies and this "
        "docstring must be revisited rather than the assertion flipped"
    )


def test_the_hold_limit_matches_the_backtest():
    """60 bars is how the backtest closed trades; a bracket has no such limit,
    so the policy has to carry it."""
    assert TradingPolicy().max_hold_bars == 60


def test_shadow_settings_are_on_and_enforce_nothing():
    """Recorded, never acted on — each improved one 86-day window and none has
    forward evidence."""
    p = TradingPolicy()
    assert p.shadow_rsi_gate and p.shadow_session_slot
    assert p.shadow_weekday and p.shadow_llm_ranking


def test_the_funnel_counts_every_candidate_once():
    decisions = (
        Decision(symbol="A", stage=TRADE),
        Decision(symbol="B", stage=CONFIRM, reason="reports earnings Tue 08/11"),
        Decision(symbol="C", stage=CONFIRM, reason="structural news (mna)"),
        Decision(symbol="D", stage=WATCH, reason="position limit reached"),
    )
    counts = summarise(decisions)
    assert counts[TRADE] == 1 and counts[CONFIRM] == 2 and counts[WATCH] == 1
    assert sum(counts.values()) == len(decisions)


def test_refusals_are_counted_by_rule():
    """A quiet day should be distinguishable from a rule that quietly stopped
    letting anything through."""
    decisions = (
        Decision(symbol="A", stage=CONFIRM, reason="reports earnings"),
        Decision(symbol="B", stage=CONFIRM, reason="reports earnings"),
        Decision(symbol="C", stage=CONFIRM, reason="structural news (mna)"),
        Decision(symbol="D", stage=TRADE),
    )
    counts = rejections(decisions)
    assert counts["reports earnings"] == 2
    assert list(counts)[0] == "reports earnings"   # most common first


def test_a_traded_candidate_is_not_a_rejection():
    assert rejections((Decision(symbol="A", stage=TRADE),)) == {}


def test_an_empty_run_summarises_cleanly():
    assert sum(summarise(()).values()) == 0
    assert rejections(()) == {}


def test_describe_names_the_rules_and_the_fingerprint():
    text = TradingPolicy().describe()
    assert "30m" in text and "stop>=1.5%" in text
    assert TradingPolicy().fingerprint in text


def test_every_entry_point_defaults_to_the_validated_configuration():
    """The disconnect this catches was real: smc_backtest defaulted to 4h with
    NO stop floor, trade.py defaulted to 4h, and every result quoted came from
    30m with a 1.5% floor. A scheduled `trade.py --submit-paper` would have
    traded a configuration nobody measured."""
    import re
    from pathlib import Path

    policy = TradingPolicy()
    wanted = {
        "--minutes": str(policy.interval_minutes),
        "--min-risk-pct": str(policy.min_risk_pct),
        "--min-rr": str(policy.min_reward_risk),
        "--fresh": str(policy.fresh_bars),
    }
    for name in ("trade", "find_setups", "smc_backtest"):
        source = Path(f"scripts/{name}.py").read_text(encoding="utf-8")
        for flag, _ in wanted.items():
            match = re.search(rf'"{flag}"[^)]*?default=([^,)]+)', source)
            if match is None and flag == "--fresh" and name == "smc_backtest":
                continue      # the backtest evaluates every setup; no freshness
            assert match, f"{name}.py has no {flag}"
            default = match.group(1).strip()
            assert "VALIDATED" in default, (
                f"{name}.py {flag} defaults to {default!r}, not the policy — "
                f"evidence and execution can drift apart"
            )


def test_tradeability_floors_are_read_from_the_policy_not_copied():
    """Three constants, four entry points, and the same bug twice.

    `trade.py` and `find_setups.py` each carried their own
    MIN_DOLLAR_VOLUME = 5_000_000.0 while TradingPolicy held the real value.
    That was fixed for the dollar floor and the comment explaining the fix was
    written directly BELOW a hardcoded `MIN_PRICE = 5.0` that had it too — in
    trade.py, find_setups.py, collect_news.py and provider_compare.py.

    `min_price` is worse than the original, because it IS a policy field and is
    therefore hashed into the fingerprint: editing it declared a fresh
    evaluation window and changed nothing live. A false signal, not a missed
    one.

    MIN_RANGE_PCT was the mirror image — enforced in `find_setups.py` and
    absent from the policy and from `trade.py`, so the preview screened 230
    symbols where the trader would act on 267.

    This test is deliberately about VALUES rather than spellings: the previous
    version asserted the literal string "TradingPolicy().min_dollar_volume"
    appeared in the source, which passed only for one way of writing the read.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    policy = TradingPolicy()
    fields = {
        "MIN_PRICE": policy.min_price,
        "MIN_DOLLAR_VOLUME": policy.min_dollar_volume,
        "MIN_RANGE_PCT": policy.min_range_pct,
    }
    entry_points = {
        "scripts/trade.py": ("MIN_PRICE", "MIN_DOLLAR_VOLUME",
                             "MIN_RANGE_PCT"),
        "scripts/find_setups.py": ("MIN_PRICE", "MIN_DOLLAR_VOLUME",
                                   "MIN_RANGE_PCT"),
        "scripts/collect_news.py": ("MIN_PRICE",),
        "scripts/provider_compare.py": ("MIN_PRICE",),
    }

    for path, names in entry_points.items():
        source = (root / path).read_text(encoding="utf-8")
        for name in names:
            found = re.findall(rf"^{name}\s*=\s*(.+?)\s*(?:#.*)?$",
                               source, re.MULTILINE)
            assert found, f"{path} no longer defines {name}"
            for expression in found:
                assert not re.fullmatch(r"[\d_.]+", expression), (
                    f"{path} hardcodes {name} = {expression} instead of "
                    f"reading the policy — evidence and execution can drift"
                )
                assert "min_" in expression or "POLICY" in expression, (
                    f"{path} sets {name} = {expression}, which does not come "
                    f"from the policy"
                )

    assert fields["MIN_DOLLAR_VOLUME"] == 20_000_000.0, (
        "the floor was raised to 20M on measurement: below it the strategy "
        "earned +0.114R and above it +0.776R, with a 2.2% permutation test"
    )
    assert fields["MIN_RANGE_PCT"] == 0.0, (
        "the range floor ships DECLARED AND OFF. It has never been measured, "
        "and enabling it removes 37 liquid names (MA, KO, PG, BRK.B) from a "
        "universe of 267 on an untested hypothesis. Thirteen untested ranking "
        "ideas have failed here. Measure it on a train/holdout split first."
    )


def test_the_range_floor_is_enforced_where_it_is_declared():
    """A declared floor that nothing reads is how MIN_RANGE_PCT started."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for path in ("scripts/trade.py", "scripts/find_setups.py"):
        source = (root / path).read_text(encoding="utf-8")
        assert "MIN_RANGE_PCT" in source.split("MIN_RANGE_PCT =", 1)[1], (
            f"{path} defines MIN_RANGE_PCT and never compares against it"
        )


def test_the_context_gate_is_free_when_it_constrains_nothing():
    """Every `require_*` flag defaults False and the live policy sets none, so
    an unconstrained call must not compute EMAs, RSI, MACD and ATR to return a
    True it could not have avoided. That was 48% of a scan's compute."""
    from unittest.mock import patch

    from scanner.features import intraday_context

    with patch.object(intraday_context, "compute") as never:
        assert intraday_context.passes(object()) is True
        never.assert_not_called()
