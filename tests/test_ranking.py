"""Ranking tests — deterministic score, and the model's advisory opinion.

Two properties matter. The deterministic score must be explainable and must
not let a tight stop buy its way up the list. The model's opinion must never
be able to emit a number or move a candidate on a claim it cannot evidence.
"""

from __future__ import annotations

import pytest

from scanner.llm.ranking import SetupOpinion, order
from scanner.screens.smc import Setup, priority


def _setup(entry=100.0, stop=98.0, target=110.0, symbol="X") -> Setup:
    return Setup(symbol=symbol, trigger_index=10, sweep_index=2, sweep_price=95.0,
                 choch_index=5, choch_price=101.0, higher_low_index=8,
                 higher_low_price=stop, entry_price=entry, stop_price=stop,
                 target_price=target)


# ---- the deterministic score -------------------------------------------

def test_a_wider_stop_ranks_higher():
    """The strongest finding in the study: wider stops are better setups, not
    merely cheaper ones."""
    wide = _setup(stop=97.0)     # 3%
    tight = _setup(stop=99.0)    # 1%
    assert priority(wide) > priority(tight)


def test_a_tight_stop_cannot_buy_rank_with_reward_risk():
    """A 0.2% stop produces a huge R:R and is exactly the trade that dies to
    friction. Capping reward:risk stops it topping the list."""
    tight = _setup(stop=99.8, target=140.0)   # enormous R:R, 0.2% stop
    solid = _setup(stop=97.5, target=106.0)   # modest R:R, 2.5% stop
    assert priority(solid) > priority(tight)


def test_room_to_run_still_counts():
    near = _setup(target=102.0)
    far = _setup(target=115.0)
    assert priority(far) > priority(near)


def test_relative_strength_is_a_light_touch_not_a_veto():
    """RS was promising but chosen from five variants, so it nudges rather
    than decides."""
    weak_but_wide = priority(_setup(stop=96.0), rs_percentile=5)
    strong_but_tight = priority(_setup(stop=99.0), rs_percentile=95)
    assert weak_but_wide > strong_but_tight


def test_score_is_deterministic():
    assert priority(_setup()) == priority(_setup())


def test_a_broken_setup_scores_zero_rather_than_crashing():
    assert priority(_setup(entry=0.0)) == 0.0


# ---- the model's opinion ------------------------------------------------

def test_conviction_orders_the_list():
    opinions = (SetupOpinion(symbol="C", conviction="weak"),
                SetupOpinion(symbol="A", conviction="strong"),
                SetupOpinion(symbol="B", conviction="neutral"))
    assert order(opinions) == ("A", "B", "C")


def test_an_unevidenced_conviction_does_not_move_anything():
    """The model must not promote or bury a candidate on a claim it could not
    quote. Ungrounded opinions fall back to neutral."""
    opinions = (SetupOpinion(symbol="A", conviction="weak", grounded=False),
                SetupOpinion(symbol="B", conviction="neutral"))
    assert order(opinions) == ("A", "B")   # alphabetical, both effectively neutral


def test_the_opinion_schema_carries_no_numbers():
    """The rule the whole project rests on: the model cannot emit a price."""
    fields = SetupOpinion.model_fields
    assert not any(f.annotation in (float, int) for f in fields.values())


def test_ordering_is_stable_for_equal_conviction():
    opinions = (SetupOpinion(symbol="B"), SetupOpinion(symbol="A"))
    assert order(opinions) == ("A", "B")


def test_no_opinions_is_not_a_crash():
    assert order(()) == ()


# ---- the sector context -------------------------------------------------

def test_the_prompt_tells_the_model_its_sector_and_standing():
    """Sector standing is arithmetic we already have. Telling the model rather
    than asking it to work it out keeps the model on the reading task."""
    from datetime import datetime, timezone

    from scanner.llm.ranking import _render
    from scanner.models import NewsItem

    item = NewsItem(id="1", source="t", queried_ticker="XOM",
                    title="Oil slides on inventory build",
                    fetched_at=datetime.now(timezone.utc))
    text = _render({"XOM": (item,)}, {"XOM": "Energy"}, {"Energy": 15.8})
    assert "Energy" in text and "leading" in text
    assert "Oil slides on inventory build" in text


def test_a_lagging_sector_is_described_as_lagging():
    from datetime import datetime, timezone

    from scanner.llm.ranking import _render
    from scanner.models import NewsItem

    item = NewsItem(id="1", source="t", queried_ticker="KO", title="x",
                    fetched_at=datetime.now(timezone.utc))
    text = _render({"KO": (item,)}, {"KO": "Consumer Staples"}, {"Consumer Staples": -19.6})
    assert "lagging" in text


def test_render_works_without_sector_data():
    """Sector lookup failing must not break ranking."""
    from datetime import datetime, timezone

    from scanner.llm.ranking import _render
    from scanner.models import NewsItem

    item = NewsItem(id="1", source="t", queried_ticker="X", title="headline",
                    fetched_at=datetime.now(timezone.utc))
    assert "headline" in _render({"X": (item,)})


def test_the_driver_field_carries_no_numbers():
    """Same rule as everything else the model emits."""
    from scanner.llm.ranking import DRIVERS, SetupOpinion

    assert set(DRIVERS) == {"company", "sector", "market", "none"}
    assert SetupOpinion(symbol="X").driver == "none"
