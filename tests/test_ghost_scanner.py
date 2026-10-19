"""ghost_scanner: the paused sweep scanner replayed on paper under its own rules."""

from datetime import datetime, timedelta

from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import EXCHANGE_TZ, IntradayBars
from scanner.ops import ghost_scanner as gs
from scanner.screens.smc import Setup

POLICY = TradingPolicy()


def _times(days, per_day=13):
    """Session bars 09:30..15:30 ET on the given weekdays."""
    out = []
    for d in days:
        start = datetime(2026, 10, d, 9, 30, tzinfo=EXCHANGE_TZ)
        out += [start + timedelta(minutes=30 * i) for i in range(per_day)]
    return tuple(out)


def _bars(symbol, closes, days=(13, 14, 15)):
    times = _times(days)[:len(closes)]
    return IntradayBars(symbol=symbol, interval_minutes=30, alignment="session",
                        times=times, opens=tuple(closes), highs=tuple(c + 0.1 for c in closes),
                        lows=tuple(c - 0.1 for c in closes), closes=tuple(closes),
                        volumes=(1e6,) * len(closes))


def _setup(symbol, i, entry=100.0, stop=97.0, target=106.0):
    return Setup(symbol=symbol, trigger_index=i, sweep_index=0, sweep_price=stop,
                 choch_index=0, choch_price=entry, higher_low_index=0,
                 higher_low_price=stop, entry_price=entry, stop_price=stop,
                 target_price=target)


def test_target_hit_is_scored_in_r():
    closes = [100.0] * 5 + [103.0, 105.95] + [105.95] * 10
    bars = _bars("AAA", closes)
    [t] = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 4),)}, {"AAA": "Tech"},
                    POLICY, since=bars.times[0], existing=())
    assert t.reason == "target" and t.exit_price == 106.0
    assert round(t.r, 2) == 2.0


def test_trade_still_open_stays_open_and_is_marked():
    closes = [100.0] * 5 + [101.5] * 3
    bars = _bars("AAA", closes)
    [t] = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 4),)}, {"AAA": "Tech"},
                    POLICY, since=bars.times[0], existing=())
    assert t.exit_at is None
    assert round(gs.marked_r(t, 101.5), 2) == 0.5


def test_setup_on_a_session_last_bar_is_refused():
    closes = [100.0] * 20
    bars = _bars("AAA", closes)
    last_of_day = 12                       # 15:30 bar, next bar is tomorrow
    out = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", last_of_day),)}, {"AAA": "Tech"},
                    POLICY, since=bars.times[0], existing=())
    assert out == ()


def test_sector_cap_and_one_per_symbol():
    closes = [100.0] * 10
    names = ["A1", "A2", "A3", "A4"]
    bars = {s: _bars(s, closes) for s in names}
    setups = {s: (_setup(s, 3), _setup(s, 4)) for s in names}
    sectors = {s: "Tech" for s in names}
    out = gs.replay(bars, setups, sectors, POLICY, since=bars["A1"].times[0], existing=())
    assert len(out) == POLICY.max_per_sector
    assert len({t.symbol for t in out}) == len(out)


def test_before_since_is_ignored_and_existing_is_kept():
    closes = [100.0] * 10
    bars = _bars("AAA", closes)
    first = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 2),)}, {"AAA": "Tech"},
                      POLICY, since=bars.times[0], existing=())
    again = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 2), _setup("AAA", 5))},
                      {"AAA": "Tech"}, POLICY, since=bars.times[4], existing=first)
    assert [t.entry_at for t in again] == [t.entry_at for t in first]


def test_open_trade_is_resolved_on_a_later_run():
    bars = _bars("AAA", [100.0] * 6)
    [open_t] = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 2),)}, {"AAA": "Tech"},
                         POLICY, since=bars.times[0], existing=())
    later = _bars("AAA", [100.0] * 6 + [96.0] + [96.0] * 3)
    [t] = gs.replay({"AAA": later}, {}, {"AAA": "Tech"}, POLICY,
                    since=bars.times[-1], existing=(open_t,))
    assert t.reason == "stop" and t.exit_price == 96.0   # opened through the stop


def test_friday_position_is_flat_before_the_weekend():
    # Oct 16 2026 is a Friday, Oct 19 the Monday after.
    bars = _bars("AAA", [100.0] * 26, days=(16, 19))
    [t] = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 3),)}, {"AAA": "Tech"},
                    POLICY, since=bars.times[0], existing=())
    assert t.reason == "timeout" and t.exit_at == bars.times[12]


def test_summary_counts_the_month():
    bars = _bars("AAA", [100.0] * 5 + [103.0, 105.95])
    trades = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 4),)}, {"AAA": "Tech"},
                       POLICY, since=bars.times[0], existing=())
    s = gs.summary(trades, "2026-10", last_close={})
    assert (s.closed, s.won, s.open) == (1, 1, 0)
    assert round(s.total_r, 2) == 2.0 and round(s.mean_pct, 2) == 6.0


def test_round_trip_through_json():
    bars = _bars("AAA", [100.0] * 8)
    trades = gs.replay({"AAA": bars}, {"AAA": (_setup("AAA", 2),)}, {"AAA": "Tech"},
                       POLICY, since=bars.times[0], existing=())
    assert gs.load(gs.dump(trades, through=bars.times[-1]))[0] == trades


def test_standings_show_the_ghost_scanner_outside_the_head_to_head():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("competition_script", "scripts/competition.py")
    comp = importlib.util.module_from_spec(spec)
    sys.modules["competition_script"] = comp
    spec.loader.exec_module(comp)
    ghost = {"2026-10": gs.Summary(closed=9, won=3, open=0, total_r=2.34, open_r=0.0,
                                   mean_pct=0.67)}
    text = comp.standings_text([], {}, {}, ghost)
    assert "| Ghost-scanner (sweep rules, avg per trade) | +0.67% |" in text
    assert "Head to head: tie / not enough data" in text
