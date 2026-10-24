"""The desk's research guide for Gemma (operator 2026-10-23: "create a guide for gemma everything we
tested, this is what to expect, and when, day of the week, and monthly").

Everything the weekend lab measured (TRIALS #107/#108; reports in logs/lab/), written as
EXPECTATIONS with their strength, not as rules: her rules are the plan's (PLAN_RULES). It is
shown in every trading question and saved to GUIDE_FILE for the operator. Update it when a
finding changes -- it is a record of evidence, dated.
"""

from __future__ import annotations

from pathlib import Path

GUIDE_FILE = Path("data/gemma/desk_guide.md")

GUIDE = """DESK RESEARCH GUIDE (2026-10-23; about 1,000 backtests on 2007-2024 and the account's own trades).
Information, not rules -- your rules are THE PLAN above. Strength: STRONG = held in every stock group and
both halves of history; MILD = consistent but small (tenths of a percent); WEAK = do not lean on it.

WHAT WORKS (search years only -- one hidden-year test, mid-cap momentum 10-2 rotated every 3 weeks,
FAILED in 2025: -2.5% vs its index +7.5%, so even the best of this can have a bad year)
- Big caps (S&P 500): no price rule beat the index (STRONG). That is why half the account is the SPY core.
- Momentum is the one recurring edge, best in MID caps (MILD-to-STRONG on the search years): 8-11 month
  lookbacks skipping the last 1-6 months beat the classic 12-1; very recent (1-3 month) winners do worst.
- Hold 2-4 weeks, then replace with the current leaders; 10 names did better than 20 (MILD).

EXITS
- Fast indicator sells (close under the 20/50-day, MACD, SuperTrend, Parabolic SAR) cut winners (STRONG).
- Profit protection on 2-4 week holds helped in mid and small caps, not big: keep half the gain once up
  5%, or keep 75% once up 3% (MILD-to-STRONG; NOT live yet -- the operator decides).
- The account's own record: 17 trades were up 3%+; 9 of them ended at a LOSS (average best +6.0%, result
  -0.7%). The leak was giving back run-ups and churn, not selling too early.
- Sell into strength, not weakness: after a 5%+ run-up, a big GREEN day, churning (heavy volume, narrow
  day, weak close), a high-volume reversal from a new high, or 3+ insider sales in 30 days were followed
  by the weakest returns (MILD). A big RED day or an ordinary distribution day after a run-up was
  followed by a BOUNCE (MILD) -- selling there is what gave profits back.

SELL-OFFS
- A 2%+ drop under the S&P's 10-day high comes a median 18 trading days apart (about every 3.5 weeks);
  3%+ about every 23 days; 5%+ about every 38 (STRONG). They CLUSTER: one is more likely right after
  another; calm stretches last months.
- Warnings before a 2% drop (STRONG as warnings): VIX 25-35 (1.7x the usual chance), the S&P's RSI at
  40-50 (1.47x), the S&P under its 20-day or 3%+ above it (1.3x). Calm (VIX under 13, RSI over 60): rare.
- BUT acting on them lost money in every group: out on a warning and back in when it cleared, selling
  after a 2-3% drop (-7 to -10 pts a year), buying only when calm (STRONG). Expect the dips; hold through.

CALENDAR (search years; MILD unless noted)
- Weekdays: Tuesday the strongest day in every group and both halves; Monday the weakest. Tiny
  (0.05-0.08% a day): never a reason to trade on its own. Sell-offs start a little more on Thursdays.
- Months: July, April and November strong (November mostly since 2016); September the weakest
  (-0.7 to -1.0%, worse since 2016); August weak for mid and small caps; January mixed. 2% sell-offs
  start most in June and September, least in November and August.
- Turn of the month: no effect (WEAK). Use the calendar for EXPECTATIONS (a September dip is normal),
  not as a reason to buy or sell."""


def save(path: Path = GUIDE_FILE) -> Path:
    """Write the guide for the operator to read (the same text Gemma sees)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Desk research guide for Gemma\n\n" + GUIDE + "\n", encoding="utf-8")
    return path
