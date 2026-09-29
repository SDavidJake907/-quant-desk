"""What could this test have detected? Answer it BEFORE reporting a null.

WHY THIS EXISTS
Twenty-five ideas were tested here and twenty-five reported null. Not one
stated what it was capable of detecting, so every one of them was read as "no
effect" when the honest reading was "no effect large enough for this sample to
see". Those are different claims and the difference is decisive.

Measured on the live configuration: R has a standard deviation of 1.848 against
a mean near zero. With 861 setups split into two bands the smallest provable gap
is about 0.35R, and every test here judges on a HOLDOUT of roughly 287, where
the smallest provable gap is about 0.61R.

Realistic trading edges are 0.05R to 0.2R. So the protocol was structurally
incapable of detecting anything of a plausible size, and reported that inability
as evidence of absence twenty-five times.

The protocol is not wrong. Train/holdout, permutation and sign-flip checks are
what stopped twenty-five noisy results being believed, and at roughly seventy
declared trials that discipline is necessary. But it is RIGOROUS AND
UNDERPOWERED AT THE SAME TIME: it almost never produces a false positive and
produces false negatives constantly.

USE IT BEFORE THE TEST, NOT AFTER
`minimum_detectable` answers "is this question askable with the data I have".
If the answer is larger than the effect anyone plausibly claims, the test should
not be run -- it will return null regardless of the truth, and it will still
cost a trial against the multiple-comparisons correction.
"""

from __future__ import annotations

import math
import statistics

#: Two-sided alpha 0.05 with 80% power needs roughly this many standard errors
#: between the two means. 1.96 to clear significance plus 0.84 for power.
Z_TOTAL = 2.80

#: Standard deviation of R per trade, measured on 546 resolved setups from the
#: 501-symbol Alpaca store under the live policy. Passed explicitly wherever it
#: matters; this is the default so a caller cannot forget to supply one.
MEASURED_SD = 1.848


def minimum_detectable(n_a: int, n_b: int, sd: float = MEASURED_SD,
                       z: float = Z_TOTAL) -> float:
    """Smallest difference in mean R two bands could prove, in R.

    Anything below this comes back null whether or not it is real.
    """
    if n_a < 2 or n_b < 2:
        return float("inf")
    return z * sd * math.sqrt(1.0 / n_a + 1.0 / n_b)


def needed_per_band(effect: float, sd: float = MEASURED_SD,
                    z: float = Z_TOTAL) -> int:
    """Trades PER BAND required to detect an effect of this size."""
    if effect <= 0:
        return 0
    return math.ceil(2 * (z * sd / effect) ** 2 / 2)


def verdict(observed: float, n_a: int, n_b: int, p: float,
            sd: float = MEASURED_SD) -> str:
    """One line that distinguishes 'no effect' from 'could not have seen one'.

    The distinction this module exists for. A gap smaller than the minimum
    detectable is NOT evidence of absence, and saying so is the whole point.
    """
    floor = minimum_detectable(n_a, n_b, sd)
    if p < 0.05:
        return (f"significant (p={p:.3f}); the sample could resolve "
                f"{floor:+.3f}R and this is {observed:+.3f}R")
    if abs(observed) >= floor:
        return (f"not significant (p={p:.3f}) despite exceeding the "
                f"{floor:+.3f}R resolution floor — a genuine null")
    return (f"UNDERPOWERED, not null: this sample can only resolve "
            f"{floor:+.3f}R and the observed gap is {observed:+.3f}R. "
            f"Detecting it would need ~{needed_per_band(abs(observed) or 0.2)} "
            f"trades per band. Absence of evidence, not evidence of absence.")


def describe(samples_a: list[float], samples_b: list[float],
             observed: float | None = None, p: float = 1.0) -> str:
    """Convenience wrapper: pooled sd from the data rather than the default."""
    pool = list(samples_a) + list(samples_b)
    sd = statistics.stdev(pool) if len(pool) > 2 else MEASURED_SD
    gap = (observed if observed is not None
           else statistics.fmean(samples_a) - statistics.fmean(samples_b))
    return verdict(gap, len(samples_a), len(samples_b), p, sd)
