"""The sector-strength cache must not renew its own lease.

That fault has already cost this project an evaluation window. The intraday bar
cache refreshed its own expiry and served bars up to three buckets stale, and
CCL triggered at 11:30 ET, passed every gate and was never seen. Window 5 was
superseded for it.

This cache is date-stamped rather than TTL-based, which is the right shape for
inputs that change once a day — but only if a stamp from another day is
REFUSED rather than served. These tests pin that, and that every failure path
degrades to `{}` instead of raising, because a scan must never die for want of
advisory context.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from scanner.features import sector_strength


def test_todays_file_is_served(tmp_path):
    path = tmp_path / "sector_rs.json"
    today = date(2026, 9, 26)
    sector_strength.save({"Technology": 1.5}, today=today, path=path)
    assert sector_strength.load(today=today, path=path) == {"Technology": 1.5}


def test_yesterdays_file_is_refused(tmp_path):
    """A stamp from another session is stale data, not a cache hit."""
    path = tmp_path / "sector_rs.json"
    today = date(2026, 9, 26)
    sector_strength.save({"Technology": 1.5}, today=today - timedelta(days=1),
                         path=path)
    assert sector_strength.load(today=today, path=path) == {}


def test_a_missing_file_is_empty_not_an_error(tmp_path):
    assert sector_strength.load(path=tmp_path / "absent.json") == {}


def test_corrupt_json_is_empty_not_an_error(tmp_path):
    path = tmp_path / "sector_rs.json"
    path.write_text("{not json", encoding="utf-8")
    assert sector_strength.load(path=path) == {}


def test_a_payload_of_the_wrong_shape_is_empty(tmp_path):
    """A list, or a dict without medians, must not become a partial answer."""
    path = tmp_path / "sector_rs.json"
    for body in ("[]", '{"date": "2026-09-26"}',
                 '{"date": "2026-09-26", "medians": []}'):
        path.write_text(body, encoding="utf-8")
        assert sector_strength.load(today=date(2026, 9, 26), path=path) == {}


def test_non_numeric_medians_are_dropped(tmp_path):
    path = tmp_path / "sector_rs.json"
    path.write_text(json.dumps(
        {"date": "2026-09-26",
         "medians": {"Technology": 1.5, "Energy": None, "Utilities": "flat"}}),
        encoding="utf-8")
    assert sector_strength.load(today=date(2026, 9, 26), path=path) == {
        "Technology": 1.5}


def test_the_write_is_atomic(tmp_path):
    """Half a JSON file is a corrupt cache; the bar store learned this too."""
    path = tmp_path / "sector_rs.json"
    sector_strength.save({"Technology": 1.5}, path=path)
    assert not path.with_suffix(".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["medians"]
