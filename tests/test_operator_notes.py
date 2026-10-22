from datetime import date

from scanner.advisor import operator_notes as on

RAW = """[
 {"id": "a", "from": "2026-10-21", "until": "2026-10-23", "text": "MRVL is a test."},
 {"id": "b", "from": "2026-10-01", "until": "2026-10-05", "text": "old"},
 {"id": "bad", "from": "not a date", "until": "2026-10-23", "text": "x"}
]"""


def test_read_skips_broken_rows_and_missing_files(tmp_path):
    f = tmp_path / "n.json"
    f.write_text(RAW, encoding="utf-8")
    assert [n.id for n in on.read(f)] == ["a", "b"]
    assert on.read(tmp_path / "none.json") == []
    f.write_text("{broken", encoding="utf-8")
    assert on.read(f) == []


def test_block_shows_only_active_notes_under_the_header(tmp_path):
    f = tmp_path / "n.json"
    f.write_text(RAW, encoding="utf-8")
    notes = on.read(f)
    text = on.block(notes, date(2026, 10, 22))
    assert text.startswith(on.HEADER) and "MRVL is a test." in text and "old" not in text
    assert on.block(notes, date(2026, 10, 24)) == ""


def test_every_note_becomes_a_dated_memory(tmp_path):
    f = tmp_path / "n.json"
    f.write_text(RAW, encoding="utf-8")
    mem = on.memories(on.read(f))
    assert [m["id"] for m in mem] == ["note-a", "note-b"]
    assert mem[0]["at"].startswith("2026-10-21") and "Operator's note" in mem[0]["text"]
