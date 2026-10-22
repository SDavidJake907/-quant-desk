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

| 75 | operator's deep slice: week of month 1-5 and weekday (close/overnight/day), a 16-cell RSI x VWAP sell grid, a 9-cell RSI x VWAP dip-buy grid (research_gpu/deep_slice.py) | RULES FIXED in the commit that adds this row, before the first run (efc360c) | #74's 100 random names, 5-min SIP; discover 2024-10 to 2025-09, confirm 2025-10 to 2026-10 | CALENDAR: 0 of 30 confirmed; week 4 above average both years (t +1.43 / +1.02), week 5 below both (t -1.23 / -0.86), not significant. SELL GRID: all 16 cells negative in both years; higher thresholds only fire less. BUY GRID 'vs the day's open': 18/18 'CONFIRMED' at t 37-74 -- a DESIGN ERROR, not a finding: the dip condition means the price already fell below the open, so buying lower than the open is guaranteed by the condition (look-ahead in the comparison). The valid row is raw dip buy -> close after costs: all 9 cells positive in both years (+0.06 to +0.26%/trade yr1, +0.01 to +0.18% yr2), best yr2 t +1.96 (RSI<30, 2% under VWAP), 0 confirmed; her 7 negative on 6 of 7 | FAIL (nothing confirmed); lead: the intraday dip buy, to be re-tested against random same-time entries | a slice is CONFIRMED only if year 2 has the same sign with abs t >= 2; ~70 slices searched, so ~3 false confirms are expected by chance |

| 76 | momentum settings grid: lookback 12-1 / 6-1 x top 10 / 20 / 30 x sector cap none / 3, trend filter on, live engine otherwise (research_gpu/momentum_settings.py) | RULES FIXED in the commit that adds this row, before the first run (8b31761) | panel_LIVE (500+400, 2014-26, 129 books), panel_CRASH (500, 1997-2016, 222 books) | LIVE cell: LIVE +17.4% CAGR, +2.62pp vs random t 2.25, max DD 17.9%; CRASH +17.1%, +2.04pp t 2.10, DD 11.4%; worst book -27.0% / -26.5%. 6-1 earns more (+21.3% / +22.2% CAGR, t 2.59 / 3.44) but max DD 26.9% / 31.5%; top 20/30 lower return, no lower DD; sector cap 3 lowers return and RAISES LIVE DD (22.9% at top 10) | no cell qualifies: the live setting (12-1, top 10, no cap) stays; 6-1 is a return-for-drawdown trade, not a better setting | a cell replaces live (12-1, top 10, no cap) only if on BOTH panels gap vs random >= live and max DD <= live, and LIVE NW t >= 1.5 |

| 77 | operator's 'Simple Strategy' card: 4H HH/HL trend, 4H swing-low level, 15m touch, 15m CHoCH, FVG limit entry, stop under the pullback, target the last 4H swing high; shorts mirrored (research_gpu/smc_card.py) | RULES FIXED in the commit that adds this row, before the first run (342e75c; a sign bug that gave the short side 0 trades was fixed after the first run -- mirrored prices are negative -- long numbers identical before and after) | 501 S&P names, 15m bars 2024-09 to 2026-09, 4H built from them | LONG 5,588 trades, win 22%, -0.069R t -1.59 (yr1 -0.124R, yr2 -0.011R); same-stop/target random -0.121R; card minus random +0.051R t +1.08. SHORT 5,164 trades, win 18%, -0.271R t -6.11, minus random -0.026R. Her 7 long: MU +34.9R, COHR +8.1R, SNDK +3.3R, NVDA -0.4R, VLO -1.3R, WDC -7.9R, MPC -12.9R | FAIL: loses after costs; the entry adds ~0.05R over random (not significant); shorting a rising market loses | PRIMARY long: mean R > 0 t >= 2, both years > 0, and minus a same-stop/target random control > 0 t >= 2 |

| 27 | post-earnings drift (PEAD): BEAT (surprise >= +5% and announcement return > 0) held 60 days from the close of day +2, vs ALL events (scripts/pead_test.py) | RULES FIXED 2026-10-05 before any earnings data was seen; run 2026-10-21 | Nasdaq earnings 2010-2026, point-in-time S&P 500 + MidCap 400; 38,305 events priced | BEAT -ALL +0.23pp per entry month, NW t 1.33 (198 months); halves +0.31 / +0.15pp; BEAT +0.02% abnormal (2010-17 +0.88%, 2018-26 -0.30%). Quintiles Q5-Q1 +0.43pp, t 1.18, not monotonic | **FAIL** test 1 (t < 2). Drift too small to trade at ~110 trials; the 2018-26 half is negative for BEAT. The morning ideas 'earnings winners' list stays report-only |

| 78 | operator's sister stocks (lead-lag): a leader's +3% day vs the S&P -> its 3-5 smaller sisters (suppliers, customers, peers; 9 leaders, list in the script) open t+1 to close t+5 vs the S&P, minus each sister's own average (research_gpu/sister_stocks.py) | RULES FIXED in the commit that adds this row, before the first run (8fb6b21; a report-only crash on a sister with no data was guarded after the first run, primary unchanged) | Yahoo daily 2016-10 -> 2026-10; 39 of 41 sisters priced (QRVO, CMA none) | UP lift after cost -0.24%, t -1.2 (731 shock days); halves -0.66% (t -2.0) / +0.02%; 6 of 9 leaders positive. Secondary all ~0: DOWN +0.10% t 0.4, not-caught-up +0.01%, reverse +0.03%. Same-day co-move high (refiners 0.85-1.34, oil 1.1-1.5, banks ~0.9): sisters move WITH the leader the same day, nothing left to catch up | **FAIL** tests 1 and 2. Per-pair standouts (WAL +4.8% n=37, XOM DOWN -> oil producers +2%) are 40 pairs of noise, not tested |

| 79 | "Lotto trials" (operator's name): fast movers, FORWARD shadow pool: daily, the 4 fastest liquid S&P 500 + MidCap names (20-day ATR %, $20M+/day) above their 50-day (fast_up) and the 4 fastest regardless of trend (fast_all, control), graded vs the S&P at 5/21/60 sessions (scanner/ops/ideas.py, morning ideas report) | RULES FIXED in the commit that adds this row, before the first list. History already ran in #46-55 (MAX and high vol BEAT, likely survivorship), so this is forward only | live from 2026-10-22 | PASS once 60 list days have 21-session grades: fast_up excess > 0, t >= 2 (overlap overstates t) | pending |

| 80 | operator's Pine signals on FAST movers: 11 signals from the Downloads Pine scripts (SuperTrend, Chandelier, HalfTrend, PSAR, NRTR, Schaff, EMA 9/21, Connors RSI, MFI, PVT, OBV), each plain and +ADX>25, on 15m / 1h / 5m = 66 versions; the 30 fastest names each month, point in time; next-open fills, 3 ATR stop, flat Friday, 0.05%/side; excess over the name's own drift (research_gpu/signal_lab.py) | RULES FIXED in the commit that adds this row, before the first run | 15m+1h: 501 S&P 2024-09 -> 2026-09; 5m: 107 names 2024-10 -> 2026-10 | **0 of 66 pass.** Trend flips (SuperTrend, Chandelier, HalfTrend, PSAR, NRTR, EMA cross, Schaff, PVT/OBV) lose to simply holding the fast names on every timeframe -- late in, late out, whipsawed (e.g. 15m supertrend -0.12% / -0.33% a trade vs drift, t -4.9 / -6.9). The only positive half: 1h Connors RSI pullback buys, H2 +0.14% (t 3.4), +adx +0.38% (t 3.9), but H1 -0.25% (t 1.0 / 0.1). Raw net is near zero; win rates 23-67% | **FAIL** all. Live paper books (scripts/signal_live.py) run all 66 forward from 10-22 anyway, at the operator's request; Connors 1h is the one to watch |

| 81 | operator's Pine signals on BIG CAPS: #80 unchanged (11 signals, plain/+ADX, 15m/1h/5m, same fills, stop, Friday flat, costs, excess over drift, PASS) except the universe -- the 20 largest US companies, fixed list, every month (research_gpu/signal_lab.py --bigcap) | RULES FIXED in the commit that adds this row, before the first run | 15m+1h 2024-09 -> 2026-09 (20 names); 5m 2024-10 -> 2026-10 (AAPL, NVDA, V, XOM, HD) | **0 of 66 pass.** Same picture as the fast movers: on the 20 largest companies every trend-flip and volume signal loses to simply holding them (e.g. 15m supertrend -0.12% / -0.10% a trade vs drift, t -5.5 / -6.2; 5m OBV -0.12%, t -11.9 / -20.8). Raw net per trade -0.13% to +0.50%, win 19-58%. Connors RSI, the one bright spot on fast movers (#80), is flat to negative on big caps | **FAIL** all |

| 82 | operator's best RSI levels, WALK-FORWARD: RSI mean reversion (buy under BUY, sell over SELL), grid length {2,3,5,14} x BUY {5,10,15,20,30} x SELL {50,70,90} x {none, close > EMA200} = 120 settings, searched on YEAR 1 only for fast movers and big caps on 15m and 1h; the year-1 best (highest t, >= 100 trades) judged on YEAR 2 only (research_gpu/rsi_search.py) | RULES FIXED in the commit that adds this row, before the first run | 15m/1h 2024-09 -> 2026-09 | **0 of 4 pass.** Year-1 picks: fast 15m RSI(5) buy<20 sell>70 -> year 2 -0.04%, t 1.8; big 15m RSI(14) buy<15 sell>50 -> -0.00%, t 0.9; fast 1h RSI(2) buy<10 sell>50 -> -0.07%, t 1.4; big 1h RSI(5) buy<30 sell>70 -> -0.28%, t -0.7. RSI dip-buys win 55-62% of trades on fast movers but the average trade is about zero after costs vs holding; no setting reached t 2 cleanly in year 1 | **FAIL** all. Pine export of the fast 15m pick for viewing: pine/rsi_reversion_walkforward.pine (not a proven edge) |

| 83 | Alpaca's example scalper (github.com/alpacahq/example-scalping, the operator's nearest 'pro scalper'): 1-min close crosses above the 20-min SMA -> limit buy at last trade (2-min expiry) -> sell limit at entry + $0.01, out at 15:55; A original (no stop), B + 3 x ATR(14) stop; conservative queue fills; big caps and monthly fast movers (research_gpu/scalper_test.py) | RULES FIXED in the commit that adds this row, before the first run | Alpaca SIP 1-min, 2025-10-01 -> last session | **0 of 2 pass.** Wins 85-96% of trades and loses money in every half: big caps -0.012 to -0.015% a trade (A no stop, worst -7.1%; B stop, worst -2.3%), fast movers -0.036 to -0.051% (A worst -13.9%). On $2,000 lots over the year: big -$15.4k (A) / -$21.7k (B), fast -$48.0k (A) / -$67.9k (B); t -6 to -20. One-cent wins, dollar losses; the stop makes it worse by cutting trades that would have recovered | **FAIL** -- a quick-profit exit is not an edge |

| 84 | the #83 scalper entry with DOLLAR exits: target +$5/$10/$20/$40 x stop -$10/$20/$40 on the $2,000 lot (12 pairs), 1-min, big and fast; pair selected on the first half, judged on the second (research_gpu/scalper_dollars.py) | RULES FIXED in the commit that adds this row, before the first run | data/research_1m 2025-10 -> 2026-10 | **0 of 2 pass.** Every one of 24 pair x group cells loses in both halves (-0.015 to -0.054% a trade). Bigger targets cut the win rate (95% at 1c -> 24-50% at +$40) without turning it positive; the year-1 pick +$40/-$40 -> second half big -0.029% (t -1.0), fast -0.020% (t -0.4). The ENTRY has no edge, so no exit fixes it | **FAIL** |
| 85 | the ITI liquidity sweep exactly as the live books run it (signals/sweep.py): sweep of the prior-day / 20-candle low, close back inside, 2x volume; entry next open; half at VWAP then breakeven, rest at the range high; +scalp; vs a RANDOM-entry control with the same exits; 5m and 15m, big and fast (research_gpu/sweep_test.py) | RULES FIXED in the commit that adds this row, before the first run | 15m 501 S&P 2024-09 -> 2026-09; 5m 107 names 2024-10 -> 2026-10 | **0 of 8 pass.** The sweep loses after costs everywhere: 15m big -0.12/-0.14% a trade, 15m fast -0.05/-0.07%, 5m big -0.07/-0.10%, 5m fast -0.09/-0.11%. It does beat RANDOM entries with the same exits in 7 of 8 halves by about 0.02-0.05% a trade (15m fast H1: -0.05% vs control -0.30%) -- a real but small tilt, smaller than the 0.10% round-trip cost | **FAIL** (better than random, not better than costs) |
| 86 | sweep entry (#85) with #84's 12 dollar exit pairs; pair selected on the first half per timeframe x group, judged on the second (research_gpu/sweep_test.py) | RULES FIXED in the commit that adds this row, before the first run | as #85 | **0 of 4 pass.** Year-1 picks: 15m big +$40/-$20 -> -0.058% (t -0.6); 15m fast +$40/-$20 -> -0.069% (t -1.4); 5m big +$20/-$20 -> -0.118% (t -2.5); 5m fast +$40/-$40 -> -0.123% (t -2.3). Dollar exits did not rescue the sweep | **FAIL** |

**Running total: ~801 trials (#80/#81 add 66 versions each, #82 searches 480) + #75's ~70 slices. Passes that survive everything: 0.**
**Closest:** momentum with the trend filter (#20, #23), now waiting for forward
data.

## Open, not yet run

Expect any published effect at about half its paper's size: McLean & Pontiff
(97 predictors) found returns 26% lower out of sample and 58% lower after
publication.

| candidate | prior evidence | data | status |
|---|---|---|---|
| #27 post-earnings drift (PEAD) | Ball & Brown 1968; Bernard & Thomas 1989; replicated for decades, weaker in large caps lately | Nasdaq earnings calendar, 2010→ | **RUN 2026-10-21: FAIL** (see #27 above) |
| momentum + trend, forward | #20–#25 | live paper, from window 16 | engine built, dry run (c77796b) |
| #44 TradingAgents rating as a momentum filter | none for this use; Gemma's numbers judgement measured WORSE (p=0.021) | data/momentum/ratings.jsonl, from 2026-10-06, forward only | RULE FIXED 2026-10-06 before the first rating: once 30+ rated picks have completed a 63-trading-day hold, compare return vs ^GSPC from the rating-date close for POS (Buy/Overweight) against NEG (Sell/Underweight); Hold excluded; needs 8+ in each group. PASS: POS minus NEG > 0 with Welch t >= 2. Until then record-only: no rating adds, drops or resizes a pick |
| #45 Gemma red flags on held positions (monitor v2) | Lopez-Lira & Tang: LLM headline reads predict returns, strongest after bad news; Gemma's own exit judgement measured WORSE (p=0.021) | live reviews from the first monitor-v2 run, forward only | RULE FIXED 2026-10-06 before the first live review: once 30+ red flags (f2 with a verbatim quote and a material event type) exist on held positions, flagged positions must do WORSE than unflagged ones -- swing: remaining-hold R from the review; momentum: next 20 trading days vs ^GSPC -- with t >= 2. PASS unlocks level 3 only: tighten the stop (breakeven or 0.5 x 30m ATR under price) or sell half, never a full exit on Gemma alone, and only through FREEZE.md. Until then: record and report |
| Gemma news-sentiment factor | Lopez-Lira & Tang: LLM headline scores predict returns, strongest after bad news; profits fall as LLM use spreads | news 2024->, gemma4 local | queued; start daily score logging |
| same-month seasonality | Heston & Sadka 2008 | 30y daily | queued, low priority |
| MAX / lottery stocks | Bali, Cakici & Whitelaw 2011; weaker among past winners | daily | history run in #46-55 (wrong way, survivorship); forward shadow is #79 |
| RSI(2) pullback in an uptrend (separate short-term engine) | practitioner (Connors), not peer-reviewed; cost-sensitive | daily | low priority |
