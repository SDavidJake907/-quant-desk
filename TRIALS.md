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

| 40–42 | momentum family: vol-managed, residual, overnight | 31dcad5 | 500+400 2017–26; 500 2000–15 | best: overnight +3.13pp t 2.02 (bar 2.4), survives top-5 removal, 2000–15 DD 27.7%; engine fails top-5 removal | fail (all) |

| 43 | opportunistic insider buying | 94f2ca9 | S&P 500+400, 2021–26 | OPP +1.42%/6mo (t 0.65, n=160) vs ROUT +0.54%; right direction, underpowered | fail (too few events in large caps) |

| 46–55 | GPU factor lab: 8 factors + 2 blends, one Romano-Wolf correction | 8324a69 | 500+400 2015–26; 500 1997–2016 | best adj p 0.157 (walk-forward blend); low-vol and MAX WRONG way in both periods (survivorship) | fail (all) |

| 56–65 | ten LMW chart patterns, 143,586 events | 4f1c8fd / 8066454 | 500+400 2015–26; 500 1997–2016 | no MEASURED pattern; bullish and bearish shapes give the same -0.25 to -0.84%/20d; TBOT clears RW (p 0.015) but flips sign 1997–2016 | fail (all) |

| 66 | operator's signals.py, unchanged, vs random entries under its own exits | 9b8bbd0 | 500+400 2015–26 (23,470 trades); 500 1997–2016 (19,788) | +0.06% / +0.13% per trade after 10bps, negative at 25bps; random entries +0.40% / +0.31%; signals minus random -0.44pp t -4.21 and -0.42pp t -4.14 | fail: entries significantly WORSE than random |

| 67–69 | Minervini VCP; O'Neil follow-through days; O'Neil distribution days -- the skills' own code, unchanged | 7506f2d | stocks 500+400 2015–26 & 500 1997–2016; ^GSPC 1997–2026 | VCP vs plain Stage-2 breakouts +0.21pp t 0.35 / -0.19pp; FTD 60d -0.78pp t -1.01, halves flip; 5+ distribution days -> next month BETTER (+0.87% vs +0.40%), fires 59% of months | fail (all) |

| 70 | ML lab: gradient boosting on 27 price/volume features, both tails, in R (research_gpu/ml_lab.py) | RULES FIXED in the commit that adds this row, before the first real run | 500+400 2015-26 (holdout 2022-01 on); 500 1997-2016 | LIVE holdout 2022-26 (56 mo): RankIC +0.0001 t +0.01; top-bottom +0.30%/mo t +0.58; bottom decile -0.00%/mo (no avoid list); <= -2R: bottom 9.3% vs all 9.7%; momentum alone RankIC +0.026. CRASH 2001-16: RankIC +0.021 t +2.21, spread +0.90%/mo t +3.00 -- a signal that existed and is gone | FAIL (3 of 4 checks) |

| 71 | operator's give-back exit: once the book's day gain reaches A, sell all if it gives back X of it; rebuy at the close or next open (research_gpu/giveback_test.py) | RULES FIXED in the commit that adds this row, before the first run (66ddd27) | 100 random 7-stock S&P books, 15m bars 2024-09 to 2026-09 (~478 days); her book (COHR MPC MU NVDA SNDK VLO WDC, 402 days) shown, not graded | PRIMARY -0.008%/day t -1.10 (fires 9% of days); 10 of 12 cells negative, A=0.5% loses 7-17%/yr (t -2.5 to -3.2); rebuy-next-open worse than rebuy-close in every cell (misses the overnight gap); only A=2% is ~0 (+0.001 to +0.004%/day, fires 1-3%); her book negative in all 12 cells (-0.04 to -0.23%/day) | FAIL: selling a give-back costs money; the afternoon recovers about as often as it fades |

| 72 | operator's price-level TP: resting limit sell at yesterday's high / the 5-day high, or at +$25/$50/$100 on a $3,500 position; rebuy at the close (research_gpu/price_level_tp.py) | RULES FIXED in the commit that adds this row, before the first run (759b9ab) | 501 S&P names, 15m bars 2024-09 to 2026-09 (~514 days); her 7 shown, not graded | PRIMARY prev_high -0.047%/day t -3.02, fills 50% of days, -11.8%/yr, both halves negative; high_5d -5.2%/yr t -2.85; $25 -15.4%/yr, $50 -9.3%, $100 -3.5%; her 7 negative on all 5 (-0.07 to -0.14%/day) | FAIL: a sell at the prior high gives up the days that run through it; tighter = worse |

| 73 | operator's morning window: buy the 9:30 open, sell 10:30 ET, flat otherwise; secondary: buy the close, sell 10:30 next day (research_gpu/morning_window.py) | RULES FIXED in the commit that adds this row, before the first run (5a0ea23) | 501 S&P names, 15m bars 2024-09 to 2026-09 (518 days); her 7 shown, not graded | hold +0.070%/day (+17.7%/yr); 9:30-10:30 earns +0.021%/day BEFORE costs, under the 0.10% round trip; morning minus hold -0.149%/day t -4.04 (-37.5%/yr), both halves negative; night+morning -0.091%/day t -2.88; her 7: hold +0.44%/day vs morning +0.08% before costs | FAIL: the first hour is not where the return is; costs alone exceed it |

| 74 | operator's indicator TP from today's highs: first 5-min close with RSI(14) > 75 AND > 1% above VWAP -> sell next bar open, rebuy at the close (research_gpu/rsi_vwap_tp.py) | RULES FIXED in the commit that adds this row, before the first run (07ff697) | 100 random S&P names (seed 74), Alpaca 5-min SIP 2024-10 to 2026-10 (512 days); her 7 shown per stock, not graded | PRIMARY -0.013%/day t -1.37 (-3.3%/yr), both halves negative; signal-close fill the same (t -1.33); fires 13% of stock-days. Her 7: fires 17-35% of days, ~05:55-07:50 AK, median +2.0 to +3.4% over the open; after the sell the day still ran a median +0.5 to +2.0% higher; the sell beat holding on 42-48% of fired days; per yr vs hold COHR +3.4%, NVDA +5.2%, MPC -2.9%, WDC -12.3%, VLO -13.9%, MU -19.7%, SNDK -74.2% | FAIL: closest to zero of the TP family (#71-#74) but still below hold; 2 of 7 positive is what noise gives | PRIMARY next-open fill: rule minus hold > 0 per day, t >= 2, both halves > 0; signal-close fill needs abs t >= 3.3 |

**Running total: ~106 trials. Passes that survive everything: 0.**
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
| #44 TradingAgents rating as a momentum filter | none for this use; Gemma's numbers judgement measured WORSE (p=0.021) | data/momentum/ratings.jsonl, from 2026-10-06, forward only | RULE FIXED 2026-10-06 before the first rating: once 30+ rated picks have completed a 63-trading-day hold, compare return vs ^GSPC from the rating-date close for POS (Buy/Overweight) against NEG (Sell/Underweight); Hold excluded; needs 8+ in each group. PASS: POS minus NEG > 0 with Welch t >= 2. Until then record-only: no rating adds, drops or resizes a pick |
| #45 Gemma red flags on held positions (monitor v2) | Lopez-Lira & Tang: LLM headline reads predict returns, strongest after bad news; Gemma's own exit judgement measured WORSE (p=0.021) | live reviews from the first monitor-v2 run, forward only | RULE FIXED 2026-10-06 before the first live review: once 30+ red flags (f2 with a verbatim quote and a material event type) exist on held positions, flagged positions must do WORSE than unflagged ones -- swing: remaining-hold R from the review; momentum: next 20 trading days vs ^GSPC -- with t >= 2. PASS unlocks level 3 only: tighten the stop (breakeven or 0.5 x 30m ATR under price) or sell half, never a full exit on Gemma alone, and only through FREEZE.md. Until then: record and report |
| Gemma news-sentiment factor | Lopez-Lira & Tang: LLM headline scores predict returns, strongest after bad news; profits fall as LLM use spreads | news 2024->, gemma4 local | queued; start daily score logging |
| same-month seasonality | Heston & Sadka 2008 | 30y daily | queued, low priority |
| MAX / lottery stocks | Bali, Cakici & Whitelaw 2011; weaker among past winners | daily | queued, low priority |
| RSI(2) pullback in an uptrend (separate short-term engine) | practitioner (Connors), not peer-reviewed; cost-sensitive | daily | low priority |
