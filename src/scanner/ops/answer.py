"""One honest answer to any "does X work?" question asked of this desk's data.

Not a prediction. For a sample of results (R per trade, % per day, a paired
difference) it answers the five questions every claim here has to survive:

  1. HOW BIG      the mean, with a 95% bootstrap interval
  2. LUCK?        a sign-flip (one sample) or permutation (two samples) p-value
  3. HOW MANY TRIES  the p-value after the repo's search (TRIALS.md): an idea
                  found by searching pays the multiple-testing price, Sidak
  4. ONE LUCKY TRADE?  the mean again without the best two results (SNPS-proof)
  5. ENOUGH DATA? the smallest effect this sample could prove (80% power) and
                  how many results a REALISTIC edge would need

and then the one-line answer: P(the true effect is above zero) under a
skeptical prior -- realistic edges here are a few tenths of R at most
(backtest/power.py), so a small sample is pulled toward zero, a large one is
not. Verdicts: REAL, FRAGILE, NEGATIVE, NO REALISTIC EDGE, CAN'T TELL YET.

PURE: pass the numbers in. Seeded, so the same question gives the same answer.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from scanner.models import Frozen

#: Trials recorded in TRIALS.md to date (about 101 on 2026-10-19).
TRIALS = 101
#: 1.96 to clear 5% significance plus 0.84 for 80% power.
Z_POWER = 2.80
#: Above this many results the normal approximation replaces resampling.
EXACT_UP_TO = 400
DRAWS = 4000
#: Below this many results the 95% range is Student-t, not a bootstrap: the
#: bootstrap's percentile range is too narrow on a handful of numbers (three
#: losing trades would read as "reliably negative").
SMALL = 30
#: Two-sided 95% Student-t critical values, by degrees of freedom 1..29.
T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179,
       2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064,
       2.060, 2.056, 2.052, 2.048, 2.045)

#: Skeptical prior SD per unit: the size of a realistic true effect.
PRIOR_SD = {"R": 0.10, "% per day": 0.10, "%": 1.0, "pts": 1.0}


class Answer(Frozen):
    question: str
    unit: str
    n: int
    effect: float
    low: float
    high: float
    p: float
    p_after_trials: float
    trials: int
    without_best_two: float | None
    detectable: float
    needed_n: int | None
    prob_real: float
    shrunk: float
    verdict: str
    why: str

    def text(self) -> str:
        u = self.unit
        lines = [f"{self.question}",
                 f"  answer: {self.verdict} -- {self.why}",
                 f"  how big: {self.effect:+.3f} {u} per result, 95% range "
                 f"{self.low:+.3f} to {self.high:+.3f} (n={self.n})",
                 f"  luck: p={self.p:.3f}" + (f"; after the {self.trials} ideas tried here "
                                              f"p={self.p_after_trials:.3f}" if self.trials > 1
                                              else " (not an idea found by searching)")]
        if self.without_best_two is not None:
            lines.append(f"  without the best two results: {self.without_best_two:+.3f} {u}")
        need = (f"; a realistic edge needs about {self.needed_n:,} results"
                if self.needed_n else "")
        lines += [f"  enough data: this sample can prove {self.detectable:.3f} {u} or more{need}",
                  f"  P(really above zero) {self.prob_real:.0%}; best estimate after the "
                  f"skeptical prior {self.shrunk:+.3f} {u}"]
        return "\n".join(lines)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _two_sided_normal_p(z: float) -> float:
    return 2.0 * (1.0 - _phi(abs(z)))


def after_trials(p: float, trials: int) -> float:
    """Sidak: the chance at least one of `trials` independent nulls looks this good."""
    return 1.0 - (1.0 - p) ** max(1, trials)


def shrink(effect: float, se: float, prior_sd: float) -> tuple[float, float]:
    """Normal prior N(0, prior_sd^2) x likelihood N(effect, se^2): (posterior mean,
    P(true effect > 0))."""
    if se <= 0:
        return effect, 1.0 if effect > 0 else 0.0
    precision = 1.0 / prior_sd ** 2 + 1.0 / se ** 2
    mean = (effect / se ** 2) / precision
    return mean, _phi(mean * math.sqrt(precision))


def _boot_ci(values: Sequence[float], rng: random.Random, draws: int) -> tuple[float, float]:
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(draws))
    return means[int(0.025 * draws)], means[min(draws - 1, int(0.975 * draws))]


def _sign_flip_p(values: Sequence[float], rng: random.Random, draws: int) -> float:
    observed = abs(sum(values))
    hits = sum(abs(sum(v if rng.random() < 0.5 else -v for v in values)) >= observed - 1e-12
               for _ in range(draws))
    return (hits + 1) / (draws + 1)


def _verdict(effect, low, high, p_adj, dropped, prior_sd, needed) -> tuple[str, str]:
    if high < 0:
        return "NEGATIVE", "reliably below zero: it loses"
    if p_adj < 0.05 and low > 0:
        if dropped is not None and dropped <= 0:
            return "FRAGILE", "passes, but only because of its best one or two results"
        return "REAL", "above zero even after counting every idea tried"
    if high < prior_sd:
        return "NO REALISTIC EDGE", (f"at most {high:+.3f} per result at the top of its "
                                     f"range, below a realistic edge of {prior_sd:g}")
    return "CAN'T TELL YET", (f"too few results; about {needed:,} would settle a realistic "
                              f"edge" if needed else "too few results")


def _finish(question, unit, n, effect, sd, low, high, p, trials, searched, dropped,
            prior_sd) -> Answer:
    se = sd / math.sqrt(n) if n > 0 else float("inf")
    prior = prior_sd if prior_sd is not None else PRIOR_SD.get(unit, abs(effect) or 1.0)
    p_adj = after_trials(p, trials) if searched else p
    detectable = Z_POWER * se
    needed = math.ceil((Z_POWER * sd / prior) ** 2) if sd > 0 else None
    shrunk, prob = shrink(effect, se, prior)
    verdict, why = _verdict(effect, low, high, p_adj, dropped, prior, needed)
    return Answer(question=question, unit=unit, n=n, effect=effect, low=low, high=high, p=p,
                  p_after_trials=p_adj, trials=trials if searched else 1,
                  without_best_two=dropped, detectable=detectable, needed_n=needed,
                  prob_real=prob, shrunk=shrunk, verdict=verdict, why=why)


def one_sample(values: Sequence[float], *, question: str, unit: str = "R",
               trials: int = TRIALS, searched: bool = True, prior_sd: float | None = None,
               draws: int = DRAWS, seed: int = 0) -> Answer:
    """Is the mean of these results above zero? (Paired differences go here too.)"""
    xs = [float(v) for v in values]
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two results")
    effect, sd = statistics.fmean(xs), statistics.stdev(xs)
    if n > EXACT_UP_TO:
        se = sd / math.sqrt(n)
        low, high, p = effect - 1.96 * se, effect + 1.96 * se, _two_sided_normal_p(effect / se)
    else:
        rng = random.Random(seed)
        if n < SMALL:
            half = T95[n - 2] * sd / math.sqrt(n)
            low, high = effect - half, effect + half
        else:
            low, high = _boot_ci(xs, rng, draws)
        p = _sign_flip_p(xs, rng, draws)
    dropped = statistics.fmean(sorted(xs)[:-2]) if n > 3 else None
    return _finish(question, unit, n, effect, sd, low, high, p, trials, searched, dropped,
                   prior_sd)


def two_sample(a: Sequence[float], b: Sequence[float], *, question: str, unit: str = "R",
               trials: int = TRIALS, searched: bool = True, prior_sd: float | None = None,
               draws: int = DRAWS, seed: int = 0) -> Answer:
    """Is A's mean above B's? Permutation p, bootstrap interval of the difference."""
    xa, xb = [float(v) for v in a], [float(v) for v in b]
    if len(xa) < 2 or len(xb) < 2:
        raise ValueError("need at least two results in each group")
    effect = statistics.fmean(xa) - statistics.fmean(xb)
    se = math.sqrt(statistics.variance(xa) / len(xa) + statistics.variance(xb) / len(xb))
    n = min(len(xa), len(xb))
    sd = se * math.sqrt(n)            # so detectable/needed read per matched pair
    rng = random.Random(seed)
    if len(xa) + len(xb) > EXACT_UP_TO:
        low, high, p = effect - 1.96 * se, effect + 1.96 * se, _two_sided_normal_p(effect / se)
    else:
        pooled, k = xa + xb, len(xa)
        hits = 0
        for _ in range(draws):
            rng.shuffle(pooled)
            d = statistics.fmean(pooled[:k]) - statistics.fmean(pooled[k:])
            hits += abs(d) >= abs(effect) - 1e-12
        p = (hits + 1) / (draws + 1)
        diffs = sorted(statistics.fmean(rng.choices(xa, k=len(xa)))
                       - statistics.fmean(rng.choices(xb, k=len(xb))) for _ in range(draws))
        low, high = diffs[int(0.025 * draws)], diffs[min(draws - 1, int(0.975 * draws))]
    return _finish(question, unit, n, effect, sd, low, high, p, trials, searched, None,
                   prior_sd)


def from_summary(mean: float, sd: float, n: int, *, question: str, unit: str = "R",
                 trials: int = TRIALS, searched: bool = True,
                 prior_sd: float | None = None) -> Answer:
    """Same answer from published numbers only (mean, SD, count)."""
    se = sd / math.sqrt(n)
    return _finish(question, unit, n, mean, sd, mean - 1.96 * se, mean + 1.96 * se,
                   _two_sided_normal_p(mean / se), trials, searched, None, prior_sd)


def pooled(parts: Sequence[tuple[float, int]], sd: float, *, question: str, unit: str = "R",
           trials: int = TRIALS, searched: bool = True,
           prior_sd: float | None = None) -> Answer:
    """Several samples of the same rule, (mean, n) each, weighted by count."""
    n = sum(k for _, k in parts)
    mean = sum(m * k for m, k in parts) / n
    return from_summary(mean, sd, n, question=question, unit=unit, trials=trials,
                        searched=searched, prior_sd=prior_sd)
