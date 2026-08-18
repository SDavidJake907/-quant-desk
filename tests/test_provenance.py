"""Recording which code ran, not only which rules.

The policy fingerprint answers "which RULES ran" and cannot answer "which CODE
ran". The partial-bar fix changed live behaviour materially and changed no
policy field, so trades either side of it share a fingerprint while belonging
to different experiments. An evaluation window needs both.
"""

from __future__ import annotations

from scanner.execution.provenance import UNKNOWN, build_id
from scanner.models import AuditEvent


class TestBuildId:
    def test_it_returns_something_usable(self):
        value = build_id()
        assert isinstance(value, str) and value
        assert value == UNKNOWN or len(value.split("+")[0]) >= 7

    def test_it_is_stable_within_a_process(self):
        """Read on every audit record; it cannot change mid-run."""
        assert build_id() == build_id()

    def test_a_dirty_tree_is_marked(self):
        value = build_id()
        if "+dirty" in value:
            # A run from an edited tree is not reproducible from any commit.
            assert value.endswith("+dirty")


class TestAuditCarriesIt:
    def test_the_event_model_has_a_build_field(self):
        event = AuditEvent(event="x", at=__import__("datetime").datetime.now(),
                           build="abc1234")
        assert event.build == "abc1234"

    def test_it_defaults_to_empty_rather_than_guessing(self):
        """An absent build is honestly absent. Filling it in at read time would
        attribute old records to whatever is checked out now."""
        event = AuditEvent(event="x", at=__import__("datetime").datetime.now())
        assert event.build == ""
