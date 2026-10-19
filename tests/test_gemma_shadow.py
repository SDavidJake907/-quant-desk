"""Shadow Gemma: the vote, the schema check and the paired grade."""

from scanner.advisor import gemma_shadow as gsh
from scanner.advisor.gemma_trader import Decision, Menu

MENU = Menu(stops=("tight", "normal", "wide"), targets=("none", "2R"), sizes=("small", "normal"))


def _d(*keep):
    return Decision(keep=tuple(keep))


def test_a_stock_needs_two_of_three_votes():
    out = gsh.vote([_d("MPC", "MU", "VLO"), _d("MPC", "VLO", "AMD"), _d("MPC", "TER")], 5)
    assert out == ("MPC", "VLO")


def test_vote_respects_the_limit_most_votes_first():
    out = gsh.vote([_d("A", "B", "C"), _d("C", "B", "A"), _d("C", "B")], 2)
    assert out == ("B", "C")


def test_schema_check_catches_an_answer_outside_the_schema():
    ok = {"holdings": [{"symbol": "MU", "stop": "normal", "target": "none", "size": "small"}],
          "journal": "x"}
    assert gsh.kept_schema(ok, ["MU"], MENU)
    assert not gsh.kept_schema({**ok, "holdings": [{"symbol": "ZZZ", "stop": "normal",
                                                    "target": "none", "size": "small"}]},
                               ["MU"], MENU)
    assert not gsh.kept_schema({"holdings": [{"symbol": "MU", "stop": "huge", "target": "none",
                                              "size": "small"}], "journal": "x"}, ["MU"], MENU)
    assert not gsh.kept_schema("I think MU", ["MU"], MENU)


def test_paired_grade_is_variant_minus_live_on_the_same_morning():
    recs = [gsh.ShadowRecord(question="q1", day="2026-10-20", session="pre", variant="think",
                             keep=("MU",), live_keep=("MPC",), menu=("MU", "MPC", "VLO")),
            gsh.ShadowRecord(question="q1", day="2026-10-20", session="pre", variant="vote3",
                             keep=("VLO",), live_keep=("MPC",), menu=("MU", "MPC", "VLO"))]
    spread = {("2026-10-20", ("MU",)): 2.0, ("2026-10-20", ("MPC",)): 0.5,
              ("2026-10-20", ("VLO",)): -1.0}
    assert gsh.paired(recs, "think", spread) == [1.5]
    assert gsh.paired(recs, "vote3", spread) == [-1.5]
