"""answer: checked on simulated data where the true answer is known.

An answer engine is only worth anything if it is CALIBRATED: no edge must
rarely read as REAL, a real edge must read as REAL once the data is enough,
its 95% ranges must hold the truth about 95% of the time, and its
"P(really above zero)" must come true about as often as it says.
"""

import math
import random

import pytest

from scanner.ops import answer as a


def _sample(rng, mean, sd, n):
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_no_edge_rarely_reads_as_real():
    rng = random.Random(1)
    real = sum(a.one_sample(_sample(rng, 0.0, 1.8, 30), question="null", trials=1,
                            draws=400, seed=i).verdict == "REAL" for i in range(200))
    assert real / 200 <= 0.06


def test_a_real_edge_is_found_with_enough_data():
    rng = random.Random(2)
    real = sum(a.one_sample(_sample(rng, 0.5, 1.0, 200), question="edge", trials=1,
                            draws=400, seed=i).verdict == "REAL" for i in range(40))
    assert real / 40 >= 0.9


def test_the_95_percent_range_holds_the_truth_about_95_percent_of_the_time():
    rng = random.Random(3)
    hits = 0
    for i in range(200):
        ans = a.one_sample(_sample(rng, 0.2, 1.0, 60), question="cover", trials=1,
                           draws=600, seed=i)
        hits += ans.low <= 0.2 <= ans.high
    assert 0.88 <= hits / 200 <= 0.99


def test_prob_real_comes_true_as_often_as_it_says():
    # Truths drawn from the prior itself: then P(really above zero) must be honest.
    rng = random.Random(4)
    sd, n, prior = 1.8, 100, 0.10
    se = sd / math.sqrt(n)
    said, came_true = [], []
    for _ in range(20000):
        truth = rng.gauss(0.0, prior)
        _, prob = a.shrink(rng.gauss(truth, se), se, prior)
        said.append(prob)
        came_true.append(truth > 0)
    for lo, hi in ((0.6, 0.7), (0.7, 0.8), (0.8, 0.9)):
        picked = [t for p, t in zip(said, came_true) if lo <= p < hi]
        assert len(picked) > 200
        assert abs(sum(picked) / len(picked) - (lo + hi) / 2) < 0.05


def test_one_lucky_trade_is_called_fragile_not_real():
    values = [-1.0] * 10 + [0.1] * 8 + [40.0, 40.0]
    ans = a.one_sample(values, question="one gap", trials=1, draws=800)
    assert ans.verdict != "REAL"
    assert ans.without_best_two < 0


def test_searching_pays_for_its_tries():
    assert a.after_trials(0.01, 1) == pytest.approx(0.01)
    assert a.after_trials(0.01, 101) == pytest.approx(0.638, abs=0.01)


def test_a_reliably_losing_rule_is_negative():
    ans = a.from_summary(-0.2, 1.8, 2000, question="loser", trials=1)
    assert ans.verdict == "NEGATIVE"


def test_a_big_sample_with_nothing_says_no_realistic_edge():
    ans = a.from_summary(-0.041, 1.848, 1646, question="sweep 2018-24", trials=1)
    assert ans.verdict == "NO REALISTIC EDGE"
    assert ans.prob_real < 0.5


def test_a_small_sample_says_cant_tell_and_how_many_it_needs():
    ans = a.from_summary(0.27, 1.6, 16, question="sweep live")
    assert ans.verdict == "CAN'T TELL YET"
    assert ans.needed_n == math.ceil((2.8 * 1.6 / 0.10) ** 2)


def test_two_groups_compare_by_permutation():
    rng = random.Random(5)
    ans = a.two_sample(_sample(rng, 0.6, 1.0, 80), _sample(rng, 0.0, 1.0, 80),
                       question="A beats B", trials=1, draws=800)
    assert ans.effect > 0.3 and ans.verdict == "REAL"


def test_same_question_same_answer():
    xs = [0.5, -1.0, 2.0, -0.4, 1.1, -1.0, 0.3]
    assert a.one_sample(xs, question="q", seed=9) == a.one_sample(xs, question="q", seed=9)


def test_three_losses_are_not_proof_of_a_losing_rule():
    ans = a.one_sample([-0.97, -0.45, -0.12], question="three trades", searched=False)
    assert ans.verdict == "CAN'T TELL YET"
