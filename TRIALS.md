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

## Rules added 2026-10-05 (evening), before trial #27 ran

- **The vault is forward data.** Every historical dataset here has now judged
  ideas: the 2018–24 store about 25 times, the 2y store more. None is unseen.
  From 2026-10-06 on, live and paper data is the only untouched test.
  2018–24 may still be used, but a result there is now "consistent with", not
  "confirmed on".
- **Strategies are judged on risk-adjusted terms.** From trial #27 onward, a
  strategy competes with buy-and-hold at MATCHED VOLATILITY (lever or delever
  the strategy's returns to buy-and-hold's volatility, then compare annual
  return), and on Sharpe ratio. Raw return alone judged the trend rule
  (#26) on the wrong yardstick; it stays recorded as it was judged.
- **Signals are judged cross-sectionally where possible.** Quintile sorts
  across the whole universe, with breakpoints known at the time, ahead of
  concentrated top-N books; a real signal lines up from worst to best.
- **Every result carries the trial count at the time it was run**, so the
  multiple-testing haircut is visible next to the number.

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

| 28–39 | twelve indicators inside momentum | 08303d0 | 500+400, 2016–26 | none passes; 52w-high and low-vol go the WRONG way | fail (all) |
| — | momentum bucket check (diagnostic of the control) | this commit | same | top 10 +3.43pp t 2.00; quintiles flat; without 5 names t 0.58 | edge concentrated in five stocks |

**Running total: ~73 trials. Passes that survive everything: 0.**
**Closest:** momentum with the trend filter (#20, #23), now waiting for forward
data.

## Open, not yet run

Expect any published effect at about half its paper's size: McLean & Pontiff
(97 predictors) found returns 26% lower out of sample and 58% lower after
publication.

| candidate | prior evidence | data | status |
|---|---|---|---|
| #27 post-earnings drift (PEAD) | Ball & Brown 1968; Bernard & Thomas 1989; replicated for decades, weaker in large caps lately | Nasdaq earnings calendar, 2010→ | rules fixed in `pead_test.py` before any earnings data was seen; collecting |
| momentum + trend, forward | #20–#25 | live paper, from window 16 | engine built, dry run (c77796b) |
| volatility-managed momentum | Barroso & Santa-Clara 2015 ("virtually eliminates crashes", ~2× Sharpe); Moreira & Muir 2017. Scale by MOMENTUM'S OWN 6-month realized vol -- only a market-vol simplification was tried (#24) | cached daily, 2000–26 | next; rules to be fixed first |
| residual momentum | Blitz, Huij & Martens 2011: ~2× profit, ~half the volatility; targets the five-name concentration found 10-06 | cached daily, 2000–26 | next; rules to be fixed first |
| overnight momentum | Lou, Polk & Skouras 2019: momentum earns 1.25%/mo overnight (t 4.28), -0.18% intraday | cached daily open/close | queued -- may broaden the five-name momentum edge |
| opportunistic insider buying | Cohen, Malloy & Pomorski 2012: 0.82%/mo; routine insiders ~0. Trial #5 did not separate them | Form 4, 2018-> (routine needs 3y history, so 2021->) | queued |
| Gemma news-sentiment factor | Lopez-Lira & Tang: LLM headline scores predict returns, strongest after bad news; profits fall as LLM use spreads | news 2024->, gemma4 local | queued; start daily score logging |
| same-month seasonality | Heston & Sadka 2008 | 30y daily | queued, low priority |
| MAX / lottery stocks | Bali, Cakici & Whitelaw 2011; weaker among past winners | daily | queued, low priority |
| RSI(2) pullback in an uptrend (separate short-term engine) | practitioner (Connors), not peer-reviewed; cost-sensitive | daily | low priority |
