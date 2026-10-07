"""The operator's list, kept in sync: buy what was added, sell what was removed,
never touch another strategy's position."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scanner.execution import picks
from scanner.execution.ownership import PICKS, strategy_of

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)


def test_list_file_reads_tickers_and_drops_crypto_and_junk(tmp_path):
    f = tmp_path / "picks.txt"
    f.write_text("# my leaders\nAMD\nmrvl  # chips\n\nBTC-USD\nAMD\n", encoding="utf-8")
    names, rejected = picks.read_list(f)
    assert names == ("AMD", "MRVL")
    assert rejected == ("BTC-USD",)
    assert picks.read_list(tmp_path / "missing.txt") == ((), ())


def test_plan_buys_added_and_sells_removed_and_skips_others():
    owners = {"AMD": PICKS, "NKE": PICKS, "MU": "momentum", "GS": "manual"}
    held = {"AMD", "NKE", "MU", "GS"}
    plan = picks.plan(("AMD", "MRVL", "MU", "GS", "TSM"), owners, held,
                      pending_entries={"TSM"})
    assert plan.buys == ("MRVL",)                 # TSM already ordered, AMD held
    assert plan.sells == ("NKE",)                 # ours, no longer listed
    assert dict(plan.skipped) == {"MU": "held by momentum", "GS": "held by manual"}


def test_plan_never_sells_what_another_strategy_owns():
    plan = picks.plan((), {"MU": "momentum", "X": "unknown"}, {"MU", "X"}, set())
    assert plan.sells == ()


def test_levels_wide_atr_stop_and_a_ceiling_target():
    limit, stop, target = picks.levels(last=100.0, atr_pct=4.0, atr_multiple=2.5,
                                       offset_pct=0.3, ceiling=2.0)
    assert limit == 100.30
    assert stop == 90.27                          # 2.5 x 4% of 100.30 = 10.03 below
    assert target == 200.60
    with pytest.raises(ValueError):
        picks.levels(last=100.0, atr_pct=0.0, atr_multiple=2.5, offset_pct=0.3, ceiling=2.0)


def test_client_ids_belong_to_picks():
    assert strategy_of(picks.client_order_id("AMD", NOW)) == PICKS


def test_stale_entries_are_found_by_age_and_prefix():
    orders = [
        {"id": "1", "symbol": "AMD", "side": "buy", "client_order_id": "pck-AMD-1",
         "submitted_at": (NOW - timedelta(minutes=45)).isoformat(), "filled_qty": "0"},
        {"id": "2", "symbol": "MRVL", "side": "buy", "client_order_id": "pck-MRVL-1",
         "submitted_at": (NOW - timedelta(minutes=5)).isoformat(), "filled_qty": "0"},
        {"id": "3", "symbol": "GS", "side": "buy", "client_order_id": "man-GS-1",
         "submitted_at": (NOW - timedelta(hours=3)).isoformat(), "filled_qty": "0"},
        {"id": "4", "symbol": "TSM", "side": "buy", "client_order_id": "pck-TSM-1",
         "submitted_at": (NOW - timedelta(hours=3)).isoformat(), "filled_qty": "3"},
    ]
    assert [o["id"] for o in picks.stale_entries(orders, NOW, minutes=25)] == ["1"]
