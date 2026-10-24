"""The desk research guide Gemma is shown (advisor/desk_guide.py)."""

from scanner.advisor import desk_guide


def test_guide_states_strength_and_that_it_is_not_a_rule():
    g = desk_guide.GUIDE
    assert "Information, not rules" in g and "STRONG" in g and "MILD" in g
    assert "FAILED in 2025" in g and "NOT live yet" in g
    for words in ("Tuesday", "September", "18 trading days", "Sell into strength"):
        assert words in g


def test_guide_is_short_enough_for_her_context():
    assert len(desk_guide.GUIDE.split()) < 900


def test_save_writes_the_same_text(tmp_path):
    p = desk_guide.save(tmp_path / "guide.md")
    assert desk_guide.GUIDE in p.read_text(encoding="utf-8")
