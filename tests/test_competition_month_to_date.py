"""month_to_date: Gemma's scoreboard reads the verdict's own numbers."""

from datetime import date

from scanner.ops import competition as c


def snap(day, book, pl, invested):
    return c.Snapshot(day=date(2026, 10, day), book=book, pl_total=pl, invested=invested)


def test_an_empty_book_still_has_its_month_score():
    # picks lost $600 on $10k, then sold everything (invested 0): not +0.00%
    snaps = [snap(7, "picks", -100, 10_000), snap(15, "picks", -600, 10_000),
             snap(16, "picks", -600, 0.0),
             snap(7, "gemma", 20, 15_000), snap(16, "gemma", 70, 15_000)]
    mtd = c.month_to_date(snaps, "2026-10")
    assert round(mtd["picks"], 2) == -6.0        # anchored at 0 on 10-06; -600 / 10,000
    assert round(mtd["gemma"], 2) == round(70 / 15_000 * 100, 2)


def test_no_snapshots_this_month_is_empty():
    assert c.month_to_date([snap(7, "picks", 0, 1)], "2026-11") == {}


def test_load_snapshots_keeps_the_last_run_of_a_day():
    text = ('{"day":"2026-10-16","book":"picks","pl_total":-1.0,"invested":5.0}\n'
            '{"day":"2026-10-16","book":"picks","pl_total":-2.0,"invested":5.0}\n\n')
    (only,) = c.load_snapshots(text)
    assert only.pl_total == -2.0
