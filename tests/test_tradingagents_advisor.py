from datetime import datetime, timedelta, timezone

from scanner.advisor.tradingagents import (
    WorkerResult,
    may_start,
    parse_worker_output,
    pick_tickers,
    rated_on,
    rating_event,
    scan_deadline,
)

AK = timezone(timedelta(hours=-8))


def _slate(at: str, *symbols: str) -> dict:
    return {"event": "candidate_slate", "at": at,
            "slate": [{"symbol": s} for s in symbols]}


# --- which names ------------------------------------------------------------

def test_held_positions_come_first_then_the_latest_slate():
    rows = [_slate("2026-09-30T08:03:00-08:00", "OLD"),
            _slate("2026-10-01T12:03:00-08:00", "INTC", "TSCO")]
    picked = pick_tickers(held=("AON",), rows=rows, skip=frozenset(), limit=10)
    assert picked == ("AON", "INTC", "TSCO")


def test_a_name_held_and_on_the_slate_is_rated_once():
    rows = [_slate("2026-10-01T12:03:00-08:00", "AON", "TSCO")]
    assert pick_tickers(held=("AON",), rows=rows, skip=frozenset(), limit=10) == ("AON", "TSCO")


def test_already_rated_names_are_skipped_and_the_limit_holds():
    rows = [_slate("2026-10-01T12:03:00-08:00", "A", "B", "C", "D")]
    assert pick_tickers(held=(), rows=rows, skip=frozenset({"B"}), limit=2) == ("A", "C")


def test_no_slate_and_no_positions_is_an_empty_pass_not_an_error():
    assert pick_tickers(held=(), rows=[], skip=frozenset(), limit=5) == ()


def test_rated_on_reads_only_that_day():
    rows = [{"event": "tradingagents_rating", "symbol": "AON", "at": "2026-10-05T04:10:00-08:00"},
            {"event": "tradingagents_rating", "symbol": "MLM", "at": "2026-10-02T04:10:00-08:00"},
            {"event": "position_review", "symbol": "TSCO", "at": "2026-10-05T06:00:00-08:00"}]
    assert rated_on(rows, datetime(2026, 10, 5).date()) == frozenset({"AON"})


# --- staying out of the scan's way ------------------------------------------

def test_deadline_is_just_before_the_next_scan():
    assert scan_deadline(datetime(2026, 10, 5, 6, 10, tzinfo=AK)) == datetime(2026, 10, 5, 6, 32, tzinfo=AK)
    assert scan_deadline(datetime(2026, 10, 5, 6, 40, tzinfo=AK)) == datetime(2026, 10, 5, 7, 2, tzinfo=AK)


def test_outside_scan_hours_there_is_no_deadline():
    assert scan_deadline(datetime(2026, 10, 5, 4, 0, tzinfo=AK)) is None
    assert scan_deadline(datetime(2026, 10, 5, 13, 0, tzinfo=AK)) is None


def test_a_run_does_not_start_too_close_to_a_scan():
    assert may_start(datetime(2026, 10, 5, 6, 10, tzinfo=AK), min_minutes=10)
    assert not may_start(datetime(2026, 10, 5, 6, 25, tzinfo=AK), min_minutes=10)
    assert may_start(datetime(2026, 10, 5, 4, 0, tzinfo=AK), min_minutes=10)


# --- reading the worker -----------------------------------------------------

def test_the_last_json_line_is_the_result_whatever_logging_preceded_it():
    out = 'loading...\nwarning: x\n{"ticker": "SNPS", "rating": "Buy", "seconds": 600}\n'
    result = parse_worker_output(out)
    assert result == WorkerResult(ticker="SNPS", rating="Buy", seconds=600)


def test_an_unparseable_worker_is_none_not_a_crash():
    assert parse_worker_output("Traceback ...\nValueError: boom\n") is None
    assert parse_worker_output("") is None


def test_the_rating_event_says_what_ran_and_where_the_report_is():
    at = datetime(2026, 10, 5, 4, 30, tzinfo=AK)
    result = WorkerResult(ticker="SNPS", rating="Overweight", trader="Buy",
                          seconds=612, report="logs/tradingagents/2026-10-05/SNPS.json",
                          model="gemma4:12b")
    event = rating_event(result, mode="premarket", at=at, policy="abc")
    assert event.event == "tradingagents_rating"
    assert event.symbol == "SNPS" and event.stage == "advisor"
    assert event.model == "gemma4:12b" and event.policy == "abc"
    assert event.reason.startswith("Overweight;")
    assert "mode=premarket" in event.reason and "trader=Buy" in event.reason


def test_the_rating_event_records_the_build():
    result = WorkerResult(ticker="SNPS", rating="Buy")
    event = rating_event(result, mode="live", at=datetime(2026, 10, 5, tzinfo=AK),
                         policy="abc", build="b34d2b0+dirty")
    assert event.build == "b34d2b0+dirty"


def test_the_worker_reads_the_trader_action_in_either_format():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "ta_worker", pathlib.Path(__file__).parent.parent / "scripts" / "ta_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    assert worker._ACTION.search("Action: Buy\n\nReasoning: ...").group(1) == "Buy"
    assert worker._ACTION.search("- **Action**: Sell").group(1) == "Sell"
    assert worker._ACTION.search("**Action**: **Hold**").group(1) == "Hold"


# --- cleanup ----------------------------------------------------------------

def test_checkpoints_from_before_today_are_dead():
    from scanner.advisor.tradingagents import stale_checkpoints
    now = datetime(2026, 10, 5, 4, 0, tzinfo=AK)
    files = {"INTC.db": datetime(2026, 10, 2, 11, 0, tzinfo=AK),
             "SNPS.db": datetime(2026, 10, 5, 3, 45, tzinfo=AK),
             "SNPS.db-shm": datetime(2026, 10, 2, 11, 0, tzinfo=AK)}
    # A checkpoint resumes only the same date's run; yesterday's never will.
    assert stale_checkpoints(files, now) == ("INTC.db", "SNPS.db-shm")


def test_report_folders_older_than_the_retention_go():
    from scanner.advisor.tradingagents import expired_folders
    today = datetime(2026, 10, 5).date()
    names = ["2026-08-01", "2026-09-05", "2026-09-06", "2026-10-05", "notes", "INTC"]
    # Only dated folders are ever touched: an undated one is not ours to judge.
    assert expired_folders(names, today, keep_days=30) == ("2026-08-01", "2026-09-05")


def test_the_worker_deadline_is_the_scan_deadline_or_the_ticker_timeout():
    from scanner.advisor.tradingagents import worker_deadline
    in_session = datetime(2026, 10, 5, 6, 10, tzinfo=AK)
    assert worker_deadline(in_session, timeout_s=2700) == datetime(2026, 10, 5, 6, 32, tzinfo=AK)
    off_hours = datetime(2026, 10, 5, 3, 30, tzinfo=AK)
    assert worker_deadline(off_hours, timeout_s=2700) == datetime(2026, 10, 5, 4, 15, tzinfo=AK)


def test_a_run_started_before_the_session_still_clears_the_first_scan():
    from scanner.advisor.tradingagents import worker_deadline
    early = datetime(2026, 10, 5, 4, 50, tzinfo=AK)
    assert worker_deadline(early, timeout_s=2700) == datetime(2026, 10, 5, 5, 32, tzinfo=AK)


# --- how a worker ended -----------------------------------------------------

def test_a_clean_exit_is_done():
    from scanner.advisor.tradingagents import outcome
    assert outcome(returncode=0, killed_for="") == "done"


def test_a_deadline_from_either_side_is_a_pause_not_a_failure():
    from scanner.advisor.tradingagents import DEADLINE_EXIT, outcome
    # The worker's own timer (exit 3) and the driver's kill at the scan
    # deadline are the same event: resumable from the checkpoint.
    assert outcome(returncode=DEADLINE_EXIT, killed_for="") == "paused"
    assert outcome(returncode=1, killed_for="scan") == "paused"


def test_a_crash_or_the_ticker_timeout_is_a_failure():
    from scanner.advisor.tradingagents import outcome
    assert outcome(returncode=1, killed_for="") == "failed"
    assert outcome(returncode=1, killed_for="timeout") == "failed"


def test_a_failed_name_is_not_retried_the_same_day():
    # A delisted ticker or broken data fails every time; retrying it each pass
    # would spend every window between scans on the same error.
    from scanner.advisor.tradingagents import ERROR_EVENT
    rows = [{"event": ERROR_EVENT, "symbol": "ZZZ", "at": "2026-10-05T06:40:00-08:00"}]
    assert rated_on(rows, datetime(2026, 10, 5).date()) == frozenset({"ZZZ"})


def test_the_error_event_names_the_symbol_and_why():
    from scanner.advisor.tradingagents import ERROR_EVENT, error_event
    event = error_event("ZZZ", "exit 1 | ValueError: no data", mode="live",
                        at=datetime(2026, 10, 5, 6, 40, tzinfo=AK), policy="abc", build="b")
    assert event.event == ERROR_EVENT and event.symbol == "ZZZ"
    assert event.stage == "advisor" and "ValueError" in event.reason


def test_the_worker_and_the_driver_agree_on_the_deadline_exit_code():
    import importlib.util, pathlib
    from scanner.advisor.tradingagents import DEADLINE_EXIT
    spec = importlib.util.spec_from_file_location(
        "ta_worker_exit", pathlib.Path(__file__).parent.parent / "scripts" / "ta_worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    assert worker.DEADLINE_EXIT == DEADLINE_EXIT
