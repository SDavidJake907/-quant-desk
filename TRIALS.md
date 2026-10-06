# Trial ledger: every test this repo has run, and how many shots that is

Started 2026-10-05. **Why it exists:** test enough ideas and one passes by
luck. A p of 0.03 means little after 60 tries. This file counts the tries, so
every new result is judged against the size of the search that produced it.

## The rule this ledger enforces

- **Ideas found by searching this repo's data** (filters, exits, timings)
  carry the full multiple-testing penalty. With ~65 trials to date, a
  Bonferroni bar is p < 0.05/65 ≈ 0.0008, about **|t| ≥ 3.3**. Harvey, Liu
  & Zhu (2016) argue |t| ≥ 3 for new factors in general. Nothing found by
  searching here has come close.
- **Published effects tested as published** (momentum, trend, post-earnings
  drift) were not chosen from this data. They need to hold out of sample at
  ordinary significance (t ≥ 2), with the rules fixed before the run. They are
  still trials and still counted.
- Every run is listed here, including the failures. Rules are committed
  before the run, and the commit is cited.
- **And the bar for money:** buy-and-hold S&P paid +11.0%/yr from 2007 to
  2026 (WORKLIST §32). A strategy has to beat that after costs, on data it was
  not chosen from.

## Before 2026-10-05

About **35 trials**, nearly all nulls: WORKLIST.md §§1–29 (§25 alone was
"null (24th)"). They include RS filters, VIX bands, 17 signal filters,
indicator layers, trailing and structure exits, stop buffers, sizing schemes,
model take/skip and model exits. They are counted, not re-listed.

## 2026-10-05

| # | trial | commit | data | result | verdict |
|---|---|---|---|---|---|
| 1 | sweep baseline (live rules) | 08a4bee | 2018–24 unseen | −0.041R, n=1646 | FAIL Gate 1 |
| 2 | R:R ≥ 2.0 | 08a4bee | 2018–24 | gap +0.064R, p=0.38 | fail |
| 3 | sector leadership | 08a4bee | 2018–24 | gap +0.030R, p=0.74 | fail (2y lead gone) |
| 4 | daily ATR ≥ 4.8% | 08a4bee | 2018–24 | gap +0.113R, p=0.12 | fail (weak lead) |
| 5 | insider buying, 90d | 08a4bee | 2018–24 | wrong sign | fail |
| 6 | short ratio rising | 08a4bee | 2018–24 | wrong sign | fail |
| 7 | MPC retest strategy | 573d765 | 2018–24 | account −30.9% | fail |
| 8 | sector breadth ≥ 6/11 | 573d765 | 2y, then 2018–24 | p=0.058, then p=0.40 | fail (2nd breadth null) |
| 9 | skip the opening bar | 49df1e6 | 2y, then 2018–24 | p=0.014, then p=0.65 | fail |
| 10–15 | closer targets ×6 | 850d994 | both stores | all negative | fail |
| 16–19 | take-profit ladders ×4 | cd603e3 | 2y | all worse than plain | fail |
| 20 | momentum, S&P 400 + 600 | 799c331 | 2016–26, new names | mid t 2.65, small t 1.28 | PASS (pre-set rule) |
| 21 | momentum, S&P 500 | 0983f79 | 2000–15 | vs random t 0.22, DD 62% | null in substance |
| 22–25 | crash guards ×4 | 4285dab | 2000–15 + 2016–26 | trend misses t by 0.01 | fail (all) |
| 26 | core S&P trend (Faber) | 0e63ede | 2007–26 post-pub | DD halved, −3.05pp/yr | fail |

**Running total: ~61 trials. Passes that survive everything: 0.**
**Closest:** momentum with the trend filter (#20, #23), now waiting for forward
data.

## Open, not yet run

| candidate | prior evidence | data | status |
|---|---|---|---|
| post-earnings drift (PEAD) | Ball & Brown 1968; Bernard & Thomas 1989; replicated for decades, weaker in large caps lately | Nasdaq earnings calendar, 2010→ | collecting; rules to be fixed before the run |
| momentum + trend, forward | #20–#25 | live paper, from window 16 | design |
