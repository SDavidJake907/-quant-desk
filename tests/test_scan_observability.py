"""Two silences this project could not afford.

1. THE NEWS GATE FAILS OPEN AND SAYS NOTHING
   `NewsFlag.checked` is False when Ollama could not be reached, and the gate
   then returns allowed=True with reason "news not checked". `trade.py` prints
   a reason only when a verdict REFUSES, so that string never reaches a log.
   Grepping the whole scheduled log for it returns zero hits, and it always
   will. A run with the safety check silently absent is byte-identical to a
   healthy one.

2. THE FUNNEL IS INVISIBLE
   A scan prints "0 fresh setups" whether the screen found nothing, found
   plenty and aged them all out, or found plenty and they were all already
   past their targets. Those are different failures with different fixes.

Neither of these changes what gets traded. They change what a quiet day can
be asked afterwards.
"""

from __future__ import annotations

from scanner.execution.funnel import Funnel
from scanner.execution.health import ModelHealth


class TestFunnel:
    def test_a_fresh_live_setup_survives_to_the_end(self):
        f = Funnel()
        f.scanned(500)
        f.setup_found()
        f.passed_freshness()
        f.passed_live()
        f.became_candidate()
        assert f.candidates == 1
        assert f.dropped_stale == 0
        assert f.dropped_not_live == 0

    def test_a_stale_setup_is_counted_where_it_died(self):
        """The distinction that matters on a day like 2026-08-17, when the
        screen found setups all session and every in-hours run said zero."""
        f = Funnel()
        f.setup_found()
        f.dropped_as_stale(age=13, fresh_bars=2)
        assert f.dropped_stale == 1
        assert f.candidates == 0
        assert f.oldest_rejected_age == 13

    def test_the_oldest_rejected_age_is_kept(self):
        """If every setup is 13 bars old the window is the problem; if they are
        3 bars old the scan cadence is. The number distinguishes them."""
        f = Funnel()
        for age in (3, 13, 7):
            f.setup_found()
            f.dropped_as_stale(age=age, fresh_bars=2)
        assert f.oldest_rejected_age == 13
        assert f.dropped_stale == 3

    def test_a_dead_setup_is_separated_from_a_stale_one(self):
        f = Funnel()
        f.setup_found()
        f.passed_freshness()
        f.dropped_as_not_live()
        assert f.dropped_not_live == 1
        assert f.dropped_stale == 0

    def test_the_summary_names_every_stage(self):
        f = Funnel()
        f.scanned(501)
        f.setup_found(); f.dropped_as_stale(age=13, fresh_bars=2)
        f.setup_found(); f.passed_freshness(); f.dropped_as_not_live()
        line = f.summary()
        for token in ("501", "setups 2", "stale 1", "not live 1", "candidates 0"):
            assert token in line, f"{token!r} missing from {line!r}"

    def test_a_clean_run_still_reports(self):
        """Silence on a quiet day is the thing being fixed."""
        f = Funnel()
        f.scanned(501)
        assert "setups 0" in f.summary()


class TestModelHealth:
    def test_a_reachable_model_is_healthy(self):
        h = ModelHealth(reachable=True, model_present=True, model="gemma3:12b")
        assert h.ok is True
        assert "OK" in h.banner()

    def test_an_unreachable_host_says_the_gate_is_open(self):
        h = ModelHealth(reachable=False, model_present=False, model="gemma3:12b")
        assert h.ok is False
        banner = h.banner()
        assert "NEWS GATE" in banner.upper()
        assert "gemma3:12b" in banner

    def test_a_reachable_host_missing_the_model_is_not_ok(self):
        """Ollama up but the model pulled away is the quieter failure: the
        HTTP call succeeds and the chat request is what fails."""
        h = ModelHealth(reachable=True, model_present=False, model="gemma3:12b")
        assert h.ok is False

    def test_the_banner_is_a_single_line_for_the_scheduled_log(self):
        for h in (ModelHealth(True, True, "m"), ModelHealth(False, False, "m")):
            assert "\n" not in h.banner()
