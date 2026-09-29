# stock-scanner

Deterministic-first equity scanner and paper-trading system, with a local
Gemma 3 layer that advises on every candidate, may refuse one, and reads every
open position — while remaining unable to emit a price.

*(Until 2026-09-28 this line read "kept strictly out of the trading path". That
stopped being true when `llm_veto_enabled` was turned on, and a stale sentence
at the top of a README is the same fault this project keeps finding in its own
prompts.)*

## Read this first

**This strategy is unproven, and the repository is built to keep saying so.**

Over two years of independent data, 861 setups, the configuration that actually
runs earns **+0.000R per trade** (se 0.058). Twenty-three ideas have been tested
against a train/holdout split and twenty-three were rejected. The one result
long described here as validated — the 20M liquidity floor at p=0.022 — does not
clear a Bonferroni threshold at the current trial count.

What the project is genuinely good at is knowing that. Every number below is
computed from cached bars at run time, every refusal is written to an audit log,
every evaluation window is fingerprinted, and the model layer is graded rather
than admired. Paper only. See **What is actually known** and `FREEZE.md`.

## The core idea

Cheap, deterministic, auditable work happens first. Every price, level, size
and order comes from arithmetic over bars — the same input always producing the
same answer, which is what makes a backtest mean anything and an order
explainable. The language model only ever sees news, only ever sees what
survived everything free, and is never allowed to produce a number.

```
                        PRICE PATH  (no AI anywhere)
L1  INGEST     Yahoo price bars, daily + 60m/30m intraday
L2  UNIVERSE   point-in-time S&P 1500 membership from the index change logs
L3  FEATURES   moving averages, 12-1 momentum, ATR, relative strength,
               swing structure, fixed-range volume profile
L4  SCREENS    trend / coiled / SMC sweep-CHoCH-BOS over features
L5  SIZING     shares from the stop; risk, position and buying-power caps
L6  EXECUTION  Alpaca bracket orders  ->  PAPER ACCOUNT ONLY

                        NEWS PATH  (where the model lives)
N1  INGEST     Yahoo RSS · SEC EDGAR 8-K
N2  RESOLVE    SEC ticker->CIK entity master, company-name alias matching
N3  FILTER     alias prefilter, then roundup detection by name count  [no AI]
N4  GEMMA 3    news relevance + event extraction, grounded in quotes
```

**The two paths do not meet.** Nothing Gemma outputs reaches a screen, a
backtest, an order or a price. See "Where Gemma actually sits" below — it
matters more now that the project can place orders.

### One policy, or the evidence and the execution drift apart

Three entry points once carried three different defaults for the same rules:

    smc_backtest.py    240m   k=2   rr>=1.0   stop floor 0.0     <- no floor
    find_setups.py      30m   k=2   rr>=1.5   stop floor 1.5
    trade.py           240m   k=2   rr>=1.5   stop floor 1.5     <- 4-hour bars

**`trade.py` defaulted to 4-hour bars.** The +0.218R holdout is a 30-minute
result. Every run worked only because `--minutes 30` was passed by hand; a
scheduled `trade.py --submit-paper` would have traded a configuration nobody
measured, and nothing would have errored. The backtest, meanwhile, defaulted to
no stop floor at all — the configuration measured at −0.121R after costs.

All three now read their defaults from `TradingPolicy`, and a test fails if any
of them drifts. The policy is hashed onto every audit record, so a result can
always be traced to the rules that produced it.

### Three rules that keep it honest

1. **The LLM never emits a number.** Its output schema has no numeric fields.
   Prices and percentages are rendered from data, never from the model.
2. **No citation, no claim.** Every model verdict must quote a span that
   verbatim occurs in the source text — *and* that says something. Verbatim
   alone is a weak test: the cheapest way to pass it is to copy the headline,
   which is exactly what the model does when it has no real reason. Quotes
   that only name the company, or that swallow the whole article, are dropped
   too.
3. **`as_of` on everything.** Features are computed from `bars.upto(i)`, so a
   backtest structurally cannot see the future — and the *universe* is dated
   too, so a 2022 rebalance cannot buy a company the index added in 2025.

## Setup

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
Copy-Item .env.example .env      # then set SCANNER_SEC_USER_AGENT to your email
```

SEC requires a contact string in the User-Agent or it returns 403.

Gemma 3 runs through Ollama (news triage only — not needed to trade):

```powershell
ollama pull gemma3:12b
ollama pull nomic-embed-text
```

Gemma is held with Ollama `keep_alive=45m`, which outlives the 30-minute scan
cadence — so the model stays resident through a whole session and never
reloads mid-run, then releases once scanning stops. `-1` would pin ~9.6GB of
VRAM around the clock for a model used for a few seconds every half hour.
Override with `SCANNER_LLM_KEEP_ALIVE` in `.env`.

**`nomic-embed-text` is not optional.** `embed_model` named it for the
project's entire life and it had never been pulled, so every embedding call
returned "model not found", the news pipeline took its documented fallback,
and **dedup silently never ran once**. Nothing in the fingerprint or the build
SHA could show it, which is why the model environment is now a third recorded
identifier.

Alpaca handles execution. Create an account, generate **paper** keys from the
dashboard, and put them in `.env`:

```
SCANNER_ALPACA_KEY_ID=...
SCANNER_ALPACA_SECRET_KEY=...
```

Paper keys and live keys are different credentials, and this project only ever
talks to the paper endpoint. Trading US stocks through the API is commission
free with no account minimum; the free market-data tier is IEX-only, which is
fine for order routing and wrong for volume profiles — price research here
stays on Yahoo's consolidated bars.

## Usage

```powershell
python scripts/global_board.py                      # world indices, by region
python scripts/find_candidates.py --show 15         # trend screen over S&P 500
python scripts/find_candidates.py --near-miss       # 5-of-6 names too
python scripts/backtest.py --screen all --range 5y  # incl. random control
python scripts/backtest.py --no-point-in-time       # measure survivorship bias
python scripts/dashboard.py                         # S&P 1500 heatmap -> HTML
python scripts/find_setups.py --value-sweep         # live intraday setup scan
python scripts/smc_backtest.py --index sp500        # does that setup work?
python scripts/smc_backtest.py --index sp500 --minutes 30 --value-sweep --min-risk-pct 1.5 --holdout 0.333  # chronological holdout
python scripts/smc_backtest.py --index sp500 --minutes 240 --value-sweep --holdout 0.333 --trend-context --rsi-context --macd-context  # 4H context research
python scripts/collect_intraday.py                  # grow 30m history (daily)
python scripts/check_alpaca.py                     # read-only paper connection check
python scripts/trade.py                            # scan + size, send nothing (default)
python scripts/trade.py --submit-paper             # explicitly submit paper orders
python scripts/watchdog.py -- python scripts/trade.py --dry-run  # restart scan-only jobs
python scripts/trade.py --minutes 240 --trend-context --rsi-context --macd-context --dry-run  # 4H filtered paper preview
python scripts/run_scan.py NVDA AAPL                # news triage via Gemma 3
python scripts/income.py --min-yield 2.5 --min-streak 5   # income sleeve screen
python scripts/bench_llm.py                         # is a bigger model worth it?
python scripts/brief.py AMD NVDA                    # everything known about a symbol
python scripts/test_divergence.py                   # RSI divergence at the sweep
python scripts/test_bearish_exit.py                 # bearish divergence as an exit
python scripts/test_tp_ladder.py                    # scale-out vs holding to target
python scripts/test_accumulation.py                 # rising OBV as a filter
python scripts/what_works.py                        # join decisions to outcomes
python scripts/replay_slate.py --validate           # check replay against known trades
python scripts/shadow_test.py                       # do the shadow dimensions predict?
python scripts/stale_setup_test.py                  # is a stale setup still worth taking?
python scripts/bench_triage.py                      # the PRODUCTION prompt, not a proxy
python scripts/gemma_view.py --quotes               # what the model sees, on demand
python scripts/market_calendar.py                   # closures nobody will flatten
python scripts/manage_positions.py                  # anything past max_hold_bars
python scripts/protect.py                           # positions with no live stop
powershell -File scripts/register_tasks.ps1         # market-hours scheduler
python scripts/trade.py --swing-allocation 66.7 --swing-cap 66000  # two sleeves
python -m pytest -q                                 # 801 tests
```

Price bars cache to `data/` keyed by symbol, **range and interval**, so re-runs
on the same day cost zero API requests. The old warning here said Yahoo
throttles at a few hundred requests per day; measured, 120 symbols came back in
3 seconds and 1,500 in about 40, with no throttling at all.
Index change logs cache to `data/sp500_wikitext.txt` and friends as the raw
source documents, so every reconstructed universe stays auditable against them.

### Everything tested on 2026-08-06, and what survived

Twenty variants were run against the validated baseline (30m, k=2, value
sweep, 1.5% stop floor) on one 86-day window. Edge is over the random control,
net of 20bps, on the **held-out final third**. Reading the failures matters
more than reading the winner:

| Idea | Holdout n | Holdout edge | Verdict |
|---|---|---|---|
| **baseline** | 75 | **+0.218R** | the thing being tested against |
| price > 200 EMA | 40 | −0.172R | rejected |
| price > 9 > 21 > 50 EMA | 13 | −0.386R | rejected |
| price > 21 > 50 > 200 EMA | 11 | −0.664R | rejected |
| MACD > signal | 75 | +0.159R | no help |
| MACD > signal and > 0 | 43 | +0.173R | no help |
| RSI > 60 momentum | 39 | +0.193R | no help |
| RSI 40-65 pullback | 65 | +0.294R | shadow-logged |
| coiled + volume dry-up | 26 | +0.288R | sample too small |
| RS top half | 42 | +0.448R | shadow-logged |
| RS top quartile | 29 | +0.439R | shadow-logged |
| liquidity-scaled stop floor | 167 | +0.172R | rejected |
| skip the 09:30-10:30 open | 21 | +1.269R | shadow-logged |
| skip Wednesday | 48 | +0.775R | shadow-logged |
| gap-aware fills | 75 | +0.295R | **adopted** |
| earnings calendar gate | — | — | **adopted** |

**Three findings worth more than the table.**

*Trend filters break this setup, and the reason is structural.* "Trend first —
price above the 200 EMA" is the central rule of most retail material. Applied
here it takes +0.218R to −0.172R, and the full EMA stack to −0.664R. That
advice describes breakout continuation; this is a reversal after a liquidity
sweep. Requiring price to already sit above every average requires the
reversal to have finished before entering.

*The 1.5% stop floor works for a reason we got wrong at first.* We added it
because cost in R scales as 1/risk. But *gross* expectancy also rises with the
floor, before any costs. Wider stops are not just cheaper — they are better
setups. A 2% stop means a real structural swing; a 0.5% stop means a wiggle.
Testing a liquidity-scaled floor (looser for liquid names, with per-symbol
costs) found 36% more trades and half the edge, which is what confirmed it.

*Gaps are the fat left tail, and modelling them raised the mean.* 15.6% of
exits gap through their level. Gapped stops average −1.63R against −1.00R for
clean ones, worst −3.56R; gapped targets average +2.98R. Net effect on the
mean was positive in this window — but every one of the five worst trades was
a gapped stop, and the favourable asymmetry is a property of a rising market,
not of the strategy. `--max-positions 5` is doing more work than it looks.

### Why most of these are shadow-logged rather than enabled

Twenty tests on one 86-day window. At that count, something looking good by
chance is expected rather than surprising, and several of the winners rest on
21-29 trades. "Skip Wednesday" doubles the holdout edge and has **no
mechanism** — 86 days is about twelve Wednesdays, and it contradicts the
standard claim that Wednesday is the best trend day.

So `TradeIntent` now records the RSI gate, the session slot and the weekday on
every setup **without filtering on any of them**. In three months the paper
record answers these questions on data nothing was chosen from. The flags
`--skip-open` and `--skip-weekday` exist for anyone who wants them, off by
default, labelled NOT VALIDATED.

The two adopted changes are the two that remove known risk rather than add a
signal: gap-aware fills, and the earnings gate.

### Context gates — measured, not assumed

Nine confirmation gates drawn from standard trading material, each applied to
the validated baseline (value sweep + 1.5% stop floor, 30m, k=2) and run
through the same train/holdout split. Edge is over the random control, net of
20bps:

| Gate | Train n | Train edge | Holdout n | Holdout edge |
|---|---|---|---|---|
| **baseline, no gate** | 143 | +0.278R | 75 | **+0.218R** |
| RSI 40-65 pullback | 129 | +0.327R | 65 | **+0.294R** |
| coiled + volume dry-up | 46 | +0.763R | 26 | +0.288R |
| RSI > 60 momentum | 58 | +0.205R | 39 | +0.193R |
| MACD > signal and > 0 | 84 | +0.206R | 43 | +0.173R |
| MACD > signal | 141 | +0.304R | 75 | +0.159R |
| price > 200 EMA | 53 | +0.164R | 40 | **−0.172R** |
| price > 9 > 21 > 50 EMA | 21 | −0.012R | 13 | −0.386R |
| price > 21 > 50 > 200 EMA | 19 | −0.121R | 11 | −0.664R |

**The trend filters actively hurt, and the reason is structural.** "Trend
first — price above the 200 EMA, look for buys" is advice for *breakout
continuation*. This setup is a *reversal after a liquidity sweep*: requiring
price to already sit above every moving average requires the reversal to have
finished before entering. The two ideas are opposites, and bolting one onto
the other destroys the edge — from +0.218R to −0.664R on the strictest stack.

**RSI 40-65 is the only gate that clearly helps**, and it is consistent with
the same logic: it wants the stock resting rather than extended. It also
barely costs sample (143 → 129 train), which is what separates a real filter
from one that improves an average by discarding trades.

Caveat that applies to the whole table: **nine gates were tried and the best
was reported.** With nine attempts, one looking good by chance is likely. The
RSI result earns a place in the next round of testing, not a place in the
defaults.

### 4H context research filters

The current SMC detector remains the control. The following filters are
opt-in until their holdout results are known:

- `--trend-context`: price above EMA21 > EMA50 > EMA200, with a positive EMA50 slope.
- `--rsi-context`: RSI14 in the preferred 40-65 pullback range.
- `--macd-context`: MACD above its signal line with a positive histogram.

These are confirmation gates, not independent buy signals. The paper trader
now defaults to 4H bars, but the new gates must be explicitly enabled while
their results are compared against the unfiltered SMC and random controls.

## The universe

The S&P 500, MidCap 400 and SmallCap 600 pages share a table layout, so one
parser covers all three: **1,504 unique companies, every one with a GICS
sector**. Fetching them costs about 40 seconds once a day, then nothing —
Yahoo's throttle turned out to be nowhere near where the old comment claimed.
1,503 of the 1,504 have usable price history.

Each index is audited rather than trusted. `membership_drift` compares the
reconstructed member count against today's: these are fixed-size indices, so
any drift is a missing change-log entry or a parser bug. All three sit under
2% back to 2021 — but only after that audit caught a parser that was returning
`STYLE="border-color:inherit;" | AA` as a ticker for 403 of the 1,504 names.

## What is actually known

Backtested over 5 years of S&P 500 data, 21-day holds, top 10, vs a random
control, on a **point-in-time universe** — at each rebalance the screen sees
only the companies the index actually held that day:

| Screen | Mean excess | Median | Period hit rate | Clustered t (n=46) |
|--------|------------|--------|-----------------|--------------------|
| trend  | +0.90%     | +0.35% | 63.0%           | +1.07              |
| coiled | −0.71%     | −0.67% | 37.0%           | −1.52              |
| random | +0.02%     | −0.17% | 52.2%           | +0.04              |

**Most of the trend screen's apparent edge was survivorship bias.** Rerun the
identical code against today's index membership (`--no-point-in-time`) and it
reports +2.49% at t = 2.20. Dating the universe takes that to +0.90% at
t = 1.07. Roughly two-thirds of the result was an artefact of asking which
companies are in the index *now*.

The mechanism is worth naming, because it is not only about deleted losers:
S&P adds companies *after* they have already run. Letting a 2022 rebalance buy
a name that joined in 2025 is buying the outcome of the rally that got it
added.

**The trend screen is now unproven, not promising.** t = 1.07 is not
distinguishable from luck, and it never gets near the t > 3 bar that
Harvey/Liu/Zhu argue new factors should clear given how many have been
data-mined. **The coiled screen still loses to random.**

### Known biases — read before trusting any backtest number

- **Survivorship bias — half fixed.** Membership is now point-in-time,
  reconstructed by walking the S&P change log backwards from today, so the
  *inclusion* half is gone. The *deletion* half is not: Yahoo returns 404 for
  delisted tickers, so ~30 companies that were index members during the window
  (SIVB, FRC, CERN, ATVI, TWTR …) have no bars to trade. Deletions skew toward
  failures, so what remains still flatters. The backtest prints the count
  rather than hiding it.
- **Recycled tickers.** Dead companies' symbols get reassigned — INFO and SBNY
  now serve someone else's prices. Any series that begins after its company
  left the index is dropped.
- **The change log is "selected".** Wikipedia's log is dense from ~2000 on and
  sparse before it. Reconstructions much older than that are guesses; the
  member count per year is the cheapest check that it still adds up.
- **No transaction costs — in *this* table.** `scripts/backtest.py` (the daily
  screens) still charges nothing. The intraday setup study does model spread,
  slippage and commission, and the difference it makes is large enough that
  these daily numbers should be read as gross, not net.
- **Overlapping windows.** Naive per-trade t-stats are inflated by ~√(top_n);
  use the clustered figure.
- **One regime.** Five years says nothing about a different market.

## The intraday setup screen (sweep -> CHoCH -> BOS)

A Smart Money Concepts long: liquidity sweep below a prior low, change of
character, higher low, break of structure. Entry is the CLOSE of the trigger
bar — not the order block, not the sweep low — because that is the only fill
needing no assumption. Structure is read exclusively through
`structure.known_at`, so a pivot counts only once it could have been seen, k
bars after it printed. On MU's 4h bars the median pivot was 3.9% away by the
time it was confirmable, and one sweep low was 18.4% away.

Measured on the S&P 500 against random entries with identical stops, targets
and time limits:

| Bars | k | Trades | Raw edge | With `--value-sweep` |
|------|---|--------|----------|----------------------|
| 4h | 2 | 1,963 | −0.005R | +0.001R |
| 4h | 3 | 1,173 | +0.036R | +0.003R |
| 4h | 4 | 547 | −0.039R | +0.058R |
| 30m | 2 | 1,050 | +0.116R | **+0.169R** |
| 30m | 3 | 603 | −0.085R | +0.024R |

**Raw, the pattern has no edge.** Its absolute expectancy is positive
(+0.11R to +0.26R) but random entries earned the same, because a stop-and-
target system on index stocks in a rising market makes money by itself. The
sign of the edge flips with k — a parameter that only defines how big a swing
counts. That is noise with a story attached.

**The volume-profile filter changes the picture.** Requiring the sweep to
drop below the value area low and the entry to reclaim it makes all five runs
non-negative. Four are still within noise of zero. The one real number
(+0.169R over 662 trades) comes from the *shallowest* data in the study —
Yahoo caps sub-hourly history at 86 days. Promising, not established.

### Then costs arrive

Friction is charged against price; results are measured in R. So cost in R
scales as **1/risk**, and this screen produces very tight stops — a 0.50%
harmonic mean on 30m. Net expectancy after round-trip friction:

| Friction | 30m, k=2 | 4h, k=4 |
|---|---|---|
| none | +0.275R | +0.226R |
| 9 bps | +0.097R | +0.175R |
| 20 bps | **−0.121R** | +0.114R |
| 40 bps | −0.518R | +0.002R |
| **breakeven** | **~14 bps** | **~40 bps** |

The 30-minute version has the bigger apparent edge and almost no room for
friction; the 4-hour version has a smaller edge that survives realistic costs.
Note the edge *over random* is unchanged at every level — costs hit both
equally — so "beats random" and "makes money" are different claims.

Excluding stops too tight to survive friction rescues the 30m version:

| Min stop | Trades | Gross | Net @20bps | Random @20bps |
|---|---|---|---|---|
| any | 662 | +0.275R | −0.121R | −0.290R |
| ≥1.0% | 326 | +0.224R | +0.095R | −0.026R |
| ≥1.5% | 177 | +0.332R | **+0.237R** | −0.007R |
| ≥2.0% | 94 | +0.333R | +0.257R | −0.066R |

The *mechanism* was predicted before the test — it is arithmetic, not a
discovery. The *threshold* was picked by looking at the data, so read 1.5% as
"wide enough", not as a tuned optimum, and note the sample falls to 177.

### The holdout — the first thing here to survive out-of-sample

Chronological split, most recent third held back. To keep it honest the stop
floor was re-chosen using **only the training two-thirds**, then applied
untouched to the holdout:

| Step | Result |
|---|---|
| Train picks the floor | 2.0%, edge **+0.468R** net of 20bps over 86 trades |
| Holdout, floor untouched | 43 trades, pattern **+0.192R** vs random **−0.054R**, edge **+0.247R** |

Running the earlier 1.5% floor through the same split gives +0.277R train and
**+0.219R holdout**. Two different thresholds, both surviving — the result is
not balanced on one lucky parameter.

Read it carefully, because three things temper it:

- **The decay is large.** +0.468R in training became +0.247R out of sample.
  Roughly half the training edge was the threshold fitting its own data. That
  is the size of the optimism to subtract from any in-sample number here.
- **43 trades.** Naive t = +1.45 — not significant on its own.
- **Still 86 days and one regime.** Breakeven friction is now 69.5 bps, which
  is comfortable, because 2% stops absorb costs far better than 0.5% ones.

This is the first result in the project to clear a test it could have failed.
It moves the setup from "noise with a story attached" to "a candidate that
needs more data" — which is exactly what `collect_intraday.py` is for.

Intraday depth is the binding constraint: 730 days at 60m, 86 days below it —
and that 86-day window *rolls*, so what is available today is gone in three
months. `scripts/collect_intraday.py` snapshots each day's bars into
`data/intraday/` and merges them, so the sample grows instead of sliding.
Bars still forming are never written (a half-formed bar has a real high and a
real low, both wrong, and would invent swing pivots months later), and a
re-fetch overwrites what it overlaps, so running it five times a day equals
running it once.

## Twenty-three ideas tested, twenty-three rejected — and what that means

Updated 2026-09-28. The count below is the headline number in this repository,
because it bounds how much anything else here should be believed.

Each was measured the same way: chronological train/holdout split, chosen on
train only, holdout looked at once, permutation tested, and checked for a SIGN
FLIP between halves. Four are described in detail in the sections that follow;
the rest live in `WORKLIST.md` with their numbers.

Thirteen tried to RANK one setup against another — RSI divergence, take-profit
ladders, four shadow dimensions, delayed entry, four horizons, sweep rejection
shape, and the strategy's own thesis that a pool of equal lows beats an isolated
one (+0.616R on train, **−0.674R** on holdout). Every one flipped sign.

The rest tried structure: a bearish-divergence exit, a structural lower-low
exit, gap risk, relative strength, higher-timeframe liquidity pools, an
accumulation signal borrowed from another project, VIX regime, and the joint
risk/reward geometry. All null.

**Two things did not die**, and both are statements about market STATE rather
than stock quality:

| | result |
|---|---|
| 20M liquidity floor | +0.114R below vs +0.776R above, p=0.022 |
| S&P regime filter | below both averages −0.086R (n=179), p=0.201, and it HELD its value when the sample grew 57% |

**And the trial count undermines the first one.** Declared trials are near
seventy, so a Bonferroni threshold sits around 0.0007. The liquidity floor at
p=0.022 does not clear it. The one result this project has called validated does
not survive its own multiple-comparisons correction, and the deflated Sharpe of
0.82 was computed against fifty trials rather than seventy.

### The pattern underneath all of it

Every test shows the same shape: **train half negative, holdout half positive.**
Seven hold-duration arms, five timeframes, four VIX bands, four relative-strength
bands. The variance between the two halves of the sample is larger than the
variance between any strategy variant that can be constructed.

Asked to argue against this analysis, the model gave the reading that has not
been refuted: *"it is far more probable that this difference IS a symptom of a
deeper, unaddressed flaw — perhaps a spurious correlation exploited by the
system that breaks down when conditions shift."* That was treated here as a
confound to explain away. It has not been shown to be one.

### What the traded configuration actually earns

Over two years of independent Alpaca SIP data, 861 setups, with the Friday
flatten modelled and Friday-excluded entries removed — the configuration that
actually runs:

**+0.000R per trade, standard error 0.058.**

Not proof of no edge. Fairly strong evidence of no LARGE one. Live paper trading
disagrees — ten closed trades at a mean +0.477R, +$1,860 — but one trade
(+3.197R) carries most of that and the interval contains zero.

### What the filters do contribute

Stacking them one at a time over 12,259 unfiltered setups:

```
raw pattern, no filters        12259   -0.027R
+ price >= $5                  12259   -0.027R
+ dollar volume >= 20M          6488   -0.032R
+ stop distance >= 1.5%         1889   +0.084R
+ reward:risk >= 1.5             528   +0.264R
```

The lift comes from RISK GEOMETRY, not from stock selection — which is exactly
why thirteen selection ideas failed. It costs 96% of raw setups to get there,
and that is where setup scarcity comes from.

### Six routes to "more" that are now closed, with measurements

| route | result |
|---|---|
| bigger stocks | all 22 mega-caps are already in the universe; NVDA trades $1.1B per bar |
| more stocks | all 1504 symbols adds **20** tradeable names |
| smaller stocks | $1–5B companies fail the liquidity floor by 10x |
| finer bars | 15m gives **0.56x** the setups and worse results |
| coarser bars | 90m/120m/240m significantly worse (p=0.028 / 0.006 / 0.014) |
| day trading | −0.075R; only 1% of trades reach target |

30m is now bracketed on both sides and looks like a genuine optimum rather than
a default.

## Four ideas tested against the holdout, four rejected

Each was measured with the same protocol: chronological split, choose on train
only, look at the holdout once, and report the RETENTION RATE beside the mean —
because a filter that keeps 16 of 75 trades will flatter itself whether or not
it means anything.

### RSI divergence at the sweep — no signal

The screen already requires `sweep.price < prior_low.price`, which is the price
half of bullish divergence. Only the RSI half was missing.

    TRAIN     all setups          90   +0.513R   t +2.34
              divergence > 0      41   +0.261R   t +0.91
              NO divergence       49   +0.724R   t +2.23   <- better without it
    HOLDOUT   all setups          46   +0.474R   t +1.97
              divergence > 0      26   +0.786R   t +2.34   <- better with it

The effect flips sign between halves. Read the holdout alone and it looks like a
clean win at t +2.34 keeping 57% of trades; the train half says the opposite.

### Bearish divergence as an exit — consistently worse

Long-only, so the bearish pattern can only be an exit rule.

    TRAIN     baseline            +0.513R  win 44.4%
              exit on divergence  +0.427R  win 48.9%
              of the 17 it fired on:  hold +1.251R  ->  exit +0.794R
    HOLDOUT   baseline            +0.474R  win 50.0%
              exit on divergence  +0.283R  win 50.0%
              of the 16 it fired on:  hold +1.314R  ->  exit +0.764R

It fires on WINNERS. Win rate rose 59% -> 82% on train while expectancy fell.
The mechanism is mechanical: the target IS the pre-sweep range high, and RSI is
bounded, so a second push toward a high nearly always registers less momentum
than the first. Bearish divergence near the target describes what approaching a
target looks like.

### Take-profit ladders — worse in both halves

    TRAIN                                        win      best
      hold to target        +0.380R            44.4%    +6.68R
      1/2 at 1R, rest runs  +0.108R            46.7%    +3.58R
      1/3 at 1R, 2R, 3R     +0.132R            47.8%    +1.87R
      all out at 1R         -0.052R            57.8%    +0.87R
      all out at 0.5R       -0.054R            75.6%    +0.37R
    HOLDOUT
      hold to target        +0.341R            50.0%    +3.42R
      all out at 0.5R       -0.149R            67.4%    +0.37R

"All out at 0.5R" wins 75.6% of trades and loses money. Median outcome is
-1.00R, so most setups never reach the first rung and lose a full -1R anyway;
the ladder cannot improve the left tail and only shortens the right. Each leg
is charged 0.133R for crossing the spread, which is why the baseline reads
+0.380R here rather than +0.513R.

### OBV accumulation — a clean null

From the morning report's "accumulation: rising OBV while basing".

    TRAIN     all setups        90   +0.545R   t +2.48   keeps 100%
              OBV slope > 0     56   +0.549R   t +2.07   keeps  62%
              OBV slope <= 0    34   +0.538R   t +1.39   <- control matches
    HOLDOUT   all setups        45   +0.443R   t +1.82
              OBV slope > 0     23   +0.579R   t +1.64   keeps  51%
              OBV slope <= 0    22   +0.302R   t +0.88

The control row is the tell: on train, setups WITHOUT accumulation did exactly
as well. The holdout gap of 0.277R is smaller than either standard error.

**What did not move through any of this:** the setup itself, +0.545R on train
and +0.443R on holdout in the same run. Every attempt to improve it failed
while the core kept surviving fresh splits.

## Two sleeves, one account

An income sleeve held continuously beside the swing strategy is not just an
allocation choice — it silently breaks the sizing math, because
`size_position` risks a percentage of *equity* and every backtest number here
assumes equity is the capital the strategy actually has.

Park a third of the account in dividend holdings and the swing strategy keeps
sizing off the full balance while trading out of two thirds of it. Risk per
trade rises by half, the position cap rises by half, and nothing reports an
error. The account simply becomes more concentrated than anyone chose.

    swing sleeve      66,237    26 sh   $12,846   risk $221
    whole account     99,306    40 sh   $19,762   risk $340   <- the bug

So equity is split before it reaches sizing (`execution/sleeve.py`), buying
power is capped at free sleeve capital — otherwise the broker lends against
the income holdings and undoes the split — and positions are attributed by
`client_order_id` prefix, the same mechanism that keeps a manual purchase out
of the strategy's statistics. An unrecognised position is treated as **not
ours**, deliberately: mistaking a held dividend name for a strategy position
would let the strategy size against money it never committed.

### Percentage or cap — they compound differently

    equity   99,306    percentage (66.7%)  swing 66,237     cap ($66k)  swing 66,000
    equity  150,000    percentage (66.7%)  swing 100,050    cap ($66k)  swing 66,000

A percentage floats with the account, so profit stays where it was made. Only
a `--swing-cap` holds the strategy at a fixed size and lets everything above it
fall to income — which is what "reinvest profits into dividends" actually
requires. Expressed as a percentage, that intention silently does nothing.

## The ex-dividend gate — a correction, not a filter

On the ex-dividend morning the exchange reduces the opening price by the
dividend and adjusts every resting order overnight. That is an accounting
identity, not a market move:

    KO   drop 0.61%  =  41% of a 1.5% stop
    VZ   drop 1.51%  = 100%
    MO   drop 1.56%  = 104%
    PFE  drop 1.64%  = 109%

A long held through MO's ex-date is stopped out by arithmetic. **The backtest
could not see this**: it runs on Yahoo's dividend-adjusted bars, where the drop
has been retroactively smoothed away. Live bars are not adjusted. This is a
real gap between what was measured and what will happen.

Every other context gate tested here was rejected for claiming that some
condition *predicts* better outcomes — seven of nine made the holdout worse.
This one claims nothing about the future, so it is enforced rather than
shadow-logged. It touches maybe 1-2% of trades (ex-dates are four days a year
per name) and removes a small, known, one-directional loss.

### What Alpaca's corporate-actions feed does and does not give you

Four things that look like they work and do not:

- `since`/`until` filter **payable** date, not ex-date. A September window
  returned 51 records: 51 with September payable dates, **zero** with
  September ex-dates.
- It returns nothing historical. May through August: zero records.
- Forward visibility is about one month — that is when boards declare.
- `old_rate`/`new_rate` are **split ratios**, 1/1 on every cash dividend. They
  are not the previous and new payout and cannot detect a raise or a cut.

Of 650 raw records, 81 carried zero cash, 3 had no ex-date, and 511 had already
gone ex. 61 survive validation.

## One page per symbol

    python scripts/brief.py AMD NVDA MU

Assembles what the system already computes and then discards a line at a time:
trend (20/50/200 EMA with distance), momentum (RSI daily and intraday, ATR%),
relative strength, sector, liquidity against the floors, the setup with its
entry/stop/target/RR and freshness, position size, earnings and ex-dividend
risk, dividend record, and Gemma's read on the news.

**What it deliberately omits:** probability estimates, T1/T2 target ladders,
analyst grades, implied volatility, fundamentals. Not because they would not
be useful — because this project has no source for them, and a confident number
with nothing behind it is worse than a blank space. Where something is unknown
it says unknown.

The distinction that matters is freshness. A brief on AMD reported the setup as
`6 bars ago — stale, not tradeable` against a 2-bar window. Reports that hand
you entry levels without saying whether the signal is still live are describing
a trade that has already gone.

## The income screen — record, not yield

Yield is a ratio, and it rises when the denominator collapses, so a high-yield
screen selects for distress:

    T    4.69%   cut, then frozen        rejected
    MO   6.26%   raised 8 years running  ranked
    O    5.20%   raised 8 years running  ranked

`scripts/income.py` pairs yield with the record behind it — consecutive raises,
growth rate, and a hard rejection for any cut in the window. Of 501 S&P 500
names, 411 pay a dividend, 91 cut, and 79 clear 2.5% yield with an 8-year
streak. The rejection list is where the work shows:

    LYB  6.71%  2022:9.90, 2023:4.94  -> a special dividend, not a raise
    PGR  6.45%  2022:0.40, 2025:4.90  -> wildly variable
    ARE  7.18%  ...2024:5.19, 2025:4.68  -> eight raises, then a cut

Two bugs found building it, both of which rejected good names:

**Realty Income was flagged as a cutter.** It has raised for 25+ years. Monthly
payers do not reliably pay twelve times per calendar year — O paid 11 times in
2024 and 13 in 2025, because a December declaration can settle in January. Raw
totals read 3.06 -> 2.87 -> 3.49, so calendar bucketing counted schedule noise
as a cut. Years are now normalised to the schedule's usual cadence.

**Every streak read exactly "3y"** — a ceiling, not a measurement. A five-year
window yields four complete years, so three was the maximum possible. The
default is now ten years.

A cut in the *first* year of any window stays invisible, because that year is
dropped as partial. That limitation is pinned by a test rather than hidden.

Payout ratio is deliberately absent: it needs earnings data this project does
not carry, and a payout ratio computed from a bad EPS figure is worse than none
because it looks authoritative.

## The evaluation window — read `FREEZE.md` before changing anything

This project is inside a **frozen evaluation window**. The rules that decide
what trades, and the code that carries them out, do not change while one is
open — and if one of them must, the window ends and is reported as truncated.

    window               5
    policy fingerprint   5b091652a8ab
    opened               2026-09-25
    ends                 2026-12-04, fixed in advance

The end date is the last session before 23x5 trading begins on 2026-12-06. It
was fixed from a published exchange schedule before a single trade existed,
which is what makes it a boundary rather than a decision: it cannot move, and
no result can bring it closer. Stopping when the numbers look good is how a
random walk becomes a strategy.

**Every record carries three identifiers**, because each has caught something
the others could not:

- the **policy fingerprint** says which RULES ran
- the **build SHA** says which CODE ran — the partial-bar fix changed live
  entry prices and touched no policy field
- the **model environment** says which MODELS were installed — `embed_model`
  named a model that had never been pulled, so dedup silently never ran

`FREEZE.md` holds the stop rules, the list of files that must not change, and
a log of every window and why each ended. `WORKLIST.md` holds what is open and
what has been measured and closed.

### Expect this to be slow

Across 114 scans the funnel has produced **11 candidates from 8,479 setups**.
That is not a defect: 99.7% are rejected as stale, and `fresh_bars = 2` was
measured and left alone — see `WORKLIST.md` §8. A single bar of delay
disqualifies more than half of all setups on the policy's own risk geometry.

Scarcity is the honest output of this strategy. The window will end on its
date with roughly twenty trades, and that is stated up front rather than
discovered in December.

## Running unattended

    powershell -ExecutionPolicy Bypass -File scripts/register_tasks.ps1
    powershell -ExecutionPolicy Bypass -File scripts/register_tasks.ps1 -Remove

Four Windows tasks:

    stock-scanner-scan       05:33-12:03 AK, every 30 min   scan + paper orders
    stock-scanner-data       13:00 AK daily                 grow 30m bar history
    stock-scanner-outcomes   13:30 AK daily                 write realized R back
    stock-scanner-flatten    Fri 08:45 + 11:45 AK           close before the weekend

The flatten has two triggers because a 13:00 ET early close needs one at 12:45
and a normal bell needs one at 15:45. Each asks the broker calendar whether
the close is within 25 minutes, so on a normal Friday the early one exits
having done nothing.

### The scan is three stages, in this order

    protect.py --submit         re-arm any position with no live stop
    manage_positions.py --exec  close anything past max_hold_bars
    trade.py --submit-paper     scan, size, send

Order matters. A naked position is the most urgent thing in the account, so
it is handled before anything else. The hold limit runs next because the
backtest frees the slot at the same bar it exits, so closing first lets this
cycle reuse it — which is what was measured. New entries come last.

Both of the first two were written long before they were scheduled, and
neither ran: `max_hold_bars = 60` is how +0.218R was measured, 5.6% of
holdout trades exited on it, and **not one live trade ever did**. A limit
declared in policy and enforced nowhere is not a limit.

05:30 Alaska is 09:30 Eastern, and both zones observe DST together, so the
4-hour offset holds year-round with no seasonal correction.

**Offset by three minutes on purpose.** Bars close on the hour and the half
hour. A task firing at :00 races the data provider to finalise the bar it
needs, and a run at exactly 05:30 reads YESTERDAY's bars because the session's
first bar does not close until 06:00.

Three things that went wrong building this, all worth knowing:

- **`schtasks.exe` silently ignores `/ET` on a MINUTE schedule.** It reports
  SUCCESS and creates a task repeating every 30 minutes FOREVER, firing all
  night. The only evidence is `Repeat: Until: Duration: Disabled` in the
  verbose query. The PowerShell cmdlets set a real duration.
- **Working directory.** Config paths are relative, so a task launched from
  System32 writes its audit log where nobody looks — and the scan appears to
  work while recording nothing. Hence the `.bat` wrappers.
- **`MultipleInstances IgnoreNew`.** A slow scan must never have a second copy
  start on top of it: two runs could each read the account before the other
  ordered, and size two positions against the same free sleeve capital.

### It evaluates when the market is shut

A refusal to trade is a reason not to send an order, not a reason to stop
looking. Most material news — earnings, mergers, guidance — lands when the
market is closed, so reading it while nothing can be traded is when reading is
free. Any failed gate now downgrades to evaluation-only rather than returning.

That needed a second fix to be worth anything: `fresh_bars` counts BARS, and no
bars print overnight, so "the last 2 bars" means the last 2 bars of yesterday
forever. An overnight scan would have reported zero setups every half hour and
looked like a quiet market rather than a stopped clock. Outside market hours
the window widens to one session (13 bars at 30m).

Those are a WATCH list, never trade candidates. The measured entry is the close
of the trigger bar; entering the same setup at tomorrow's open is a different
trade at a different price with a stop that may already be broken.

## Execution (paper only)

`scripts/trade.py` turns setups into bracket orders on an Alpaca **paper**
account. It defaults to scan-only mode. `--submit-paper` is required to send
orders, and there is no `--live` flag.

### Watchdog

The watchdog can restart crashed collectors and scan-only jobs with bounded
exponential backoff. It records starts, crashes, restarts, and final status in
`logs/watchdog.jsonl`. It refuses `--submit-paper` and `--live`; an uncertain
broker response must be reconciled against Alpaca before restarting.

Three things it gets right that an unattended trader must:

- **Bracket orders.** Entry, stop and target go as one unit, so the position
  is never alive without a stop behind it — not even in the window where the
  process could die.
- **Idempotent order ids** derived from the symbol and the trigger bar, not
  the clock. A restart, a retry, or two scans of the same bar cannot open the
  same trade twice.
- **Guards that refuse rather than adjust**: market closed, equity floor,
  daily loss limit, max concurrent positions, already-held, already-ordered.

### Why 1% risk is really 0.33%, and why raising it does nothing

    risk_pct   shares   position $   risk $   real risk %   binding
        0.5%       26       12,846      221         0.33%   position cap
        1.0%       26       12,846      221         0.33%   position cap
        2.0%       26       12,846      221         0.33%   position cap
        5.0%       26       12,846      221         0.33%   position cap

The dial is disconnected. Above ~0.35% the **position cap** binds, not the risk
budget, because sizing 1% of equity behind a 1.5% stop needs 67% of the account
in one name. Raising `risk_pct` changes nothing until `max_position_pct` also
rises — and that is a decision to concentrate, not to risk more.

The evidence says the same thing from the other direction:

    HOLDOUT   n=43   mean +0.247R   sd 1.97R
    95% CI    [-0.342R , +0.836R]        <- includes zero
    trades needed for the CI to exclude zero at this effect size: ~244

    Kelly, point estimate   full +6.36%   quarter-Kelly +1.59%
    Kelly, lower CI bound   full -8.81%   quarter-Kelly -2.20%

Full Kelly assumes the edge is known. It is not — the interval spans negative.
Quarter-Kelly on the point estimate lands at 1.59%, close to where the cap
already puts it. The lever that unlocks larger size is not a config value; it
is roughly six times more trades.

### The concentration arithmetic

Position needed = `risk_pct / stop_pct`. Behind this screen's 1.5% stops, a
1% risk budget asks for **67% of the account in one name**. So the 20%
position cap binds and effective risk lands near **0.3%** per trade. That is
the cap working. Raising it is choosing concentration, not fixing a bug —
and every order prints which constraint bound it.

### It is not a day-trading strategy

Only **54%** of setups resolve inside one session; average hold is 18.7 bars,
about 1.4 trading days. Forcing a same-day exit costs two-thirds of the edge:

| Hold limit | Net @20bps | Avg bars held |
|---|---|---|
| 1 session (13 bars) | +0.064R | 10.3 |
| 2 days (26 bars) | +0.198R | 14.8 |
| ~4.6 days (60 bars) | +0.206R | 18.7 |

Positions carry overnight. Gap risk is real and is **not** modelled anywhere
in the backtest — stops are assumed to fill at the stop price, and gaps are
exactly where they do not.

## Model size is not the constraint

    model                       correct  grounded  failed   s/call
    gemma3:12b                      6/6       6/6       0     6.66
    qwen2.5:14b-instruct            6/6       6/6       0     7.34
    llama3.1:8b                     6/6       6/6       0     5.78

`scripts/bench_llm.py` runs the real triage task — deal_role plus a verbatim
quote — on cases with known answers, including the two that gate trades: a
takeover **target** must never be bought, and litigation is not a deal. All
three models scored perfectly; the 8B was fastest. Six clean headlines is a
small set and a harder one might separate them, but nothing suggests 12B is the
bottleneck. The schema already forbids the failure modes scale would help with.

AirLLM (70B on 4GB) was evaluated and rejected. It streams every layer from
disk per token, and its README names the bottleneck as disk loading. Measured
here at 3.04 GB/s: a 70B model at 4-bit is ~11.5 s/token, or 7.7 minutes for
one 40-token JSON reply, against a scan that runs every 30 minutes. It also
solves a problem this machine does not have — 14.6 GB of VRAM free, not 4.

Ollama's `keep_alive` defaults to `45m`, not `-1`. Forever pinned 8.7 GB of RAM
and 9.6 GB of VRAM around the clock for a model used seconds per half hour;
45m outlives the scan cadence so it never reloads mid-session, then releases.

## Where the model is measured, and where it is not

Updated 2026-09-28. The distinction matters more than the headline.

**Measured, and good.** `scripts/gemma_audit.py` hands the model this project's
own facts and marks its answers against conclusions established by measurement.
It scores **2/2** on the gradeable questions, and unprompted it identified the
outlier trade skewing the live mean and the optimistic fill assumption — both
later confirmed independently. Asked for an open assessment it returns
`probably_no_edge` and `fund it: no`, and its criticism of the analysis has not
been refuted.

**Measured, and clean.** `scripts/llm_compliance.py` asks the same input five
times at temperature 0 and checks each instruction against the prompt line that
states it. **Zero violations across 15 opinions**: no enum breaches, no figures
leaked into prose, no quote taken from another company's headlines, no veto
without a reason, no ungrounded veto surviving.

**Measured, and NOT good.** The veto is unstable. On a pending all-cash
acquisition — the clearest case in the prompt — the model called it `weak` 5/5
times and escalated to a veto **1/5**. The judgement is stable; the action is a
coin flip, and `llm_veto_enabled` is currently True. A gate firing on 20% of
identical inputs is not a gate.

**Not measured at all.** Whether its per-candidate ranking predicts outcomes.
`candidate_slate` records every candidate it ranked, taken and untaken, exactly
so this can be scored later. There are three slates. It also cannot be
backtested: the news store holds two days, so asking the model about 2025 gives
it either no news or today's news, and the second is lookahead that would
fabricate a spectacular result.

### What it cannot do, by construction

The ranking schema has **no numeric field**. The model cannot emit an entry, a
stop, a target or a size — those come from `screens/smc.py` and
`execution/sizing.py`. Asking whether it will follow the trading rules is the
wrong question: it cannot reach them. Every claim it makes must also quote that
company's own headline verbatim, and an ungrounded opinion has its veto
discarded and its conviction reset to neutral by `ranking.order()`.

### The rules it is given are generated, not typed

`TradingPolicy.as_prose()` renders the live rules in English with every number
taken from the field beside it. Turning a rule off removes its sentence.

This is not tidiness. Typing rules into a prompt creates a second copy that goes
stale silently, and that failed three times on 2026-09-28 alone — the capital
taper described a schedule that had changed, `ranking.py` claimed the model
"never sees prices" after it had been handed four, and the ranking prompt told
it price was not its job while asking it to judge price. A human reading a stale
docstring is mildly misled. A model reading a stale prompt reasons confidently
from a false premise and cannot check.

`scripts/review_positions.py` now runs as stage 4 of every scan, giving the
model the full desk for each open position and recording a hold/exit call to the
audit log. **Recorded, not enforced** — there is no `--execute` and no close
path. An exit is the most dangerous authority to hand over: a wrong entry costs
one R, while a model-driven close overrides the bracket, which is the only
guaranteed protection this system has.

## The model layer is not measured yet

The screens have a backtest, a random control and a clustered t-statistic. The
Gemma 3 layer has none of that — it is judged by reading its output, which is
how the trend screen's edge went unquestioned for so long.

What live runs on 2026-08-05 showed, before the filters above existed: a
four-company listicle and a market wrap both passed as material subject news
for NVDA, and 3 of 7 surviving verdicts cited the entire headline as their
evidence. The roundup detector kills the first class deterministically (11 of
200 live headlines flagged, all 11 genuine roundups). The tightened citation
rule kills the second. Neither is proof the layer is *good* — only that two
specific failures can no longer happen.

**A warning for anyone tempted to backtest this layer.** `bars.upto(i)` stops
price lookahead, and point-in-time membership stops universe lookahead, but
neither touches the model's own weights: Gemma 3 already knows that SVB failed
and that Twitter went private. Running it over historical headlines asks a
model that knows the ending. [Detecting Lookahead Bias in LLM
Forecasts](https://arxiv.org/pdf/2512.23847) proposes the mitigation —
anonymise company names and dates in the prompt — and it has to be in place
before any number from an LLM backtest means anything.

## The trade lifecycle

```
SCAN     the deterministic detector finds a setup        [no AI]
WATCH    it is ranked against its peers by priority()    [no AI, + Gemma advisory]
CONFIRM  gates run: liquidity, news, earnings, risk, duplicates
TRADE    a bracket order goes to the paper broker
```

Every candidate reaches exactly one final stage and records why it stopped.
Refusals are data: "the screen found 40 setups and refused 38, here is the
breakdown by rule" tells you far more than two orders and silence — in
particular it tells you whether a quiet day was a quiet market or a rule that
has quietly stopped letting anything through.

`TradingPolicy` holds every rule in one frozen, hashed object written beside
each decision. Six months from now the only question that matters about a
losing month is *what rules were running*, and a fingerprint answers it. Two
runs sharing a fingerprint ran the same rules; a change in results across
different fingerprints is a change in policy, not in the market.

**Ranking used to be an accident.** Candidates were sorted by trigger time —
most recent first — which is not a ranking. `priority()` now scores them on
stop width (the strongest finding in the study), reward:risk capped so a tight
stop cannot buy the top slot with a huge ratio, and relative-strength
percentile as a light touch. Crude and readable on purpose: nothing about it
was fitted, and it is deterministic so it stays testable.

**What the lifecycle deliberately does not add is a confirmation delay.**
Entry is still the close of the trigger bar, because that is what the holdout
measured. Waiting for the next bar, or for a retest, would be a different
strategy and would need its own holdout before it could claim the same
evidence.

## Where Gemma actually sits

Worth stating plainly now that the project can place orders: **the language
model has nothing to do with any trading decision.**

Gemma 3 has two jobs, both textual, neither numeric:

1. **News triage** — is this article really *about* this company, and is it
   material? Only `subject` + material survives.
2. **Ranking candidates by context** — of the setups that already passed every
   deterministic test, does any have news that makes its price structure
   unreliable? It answers `strong` / `neutral` / `weak` with a named concern
   and a quote it must be able to evidence.

The split is by competence: **price is arithmetic, context is language.**
Finding a setup is swing pivots and breaks — a model does that worse,
differently each run, and untestably. Choosing between five setups that have
already passed is partly a reading task, and that part is its own.

It never sees prices (a model shown prices starts doing arithmetic badly
instead of reading), its schema has no numeric field, and an opinion it cannot
quote falls back to neutral so it cannot bury a candidate on a hunch.

**Its ranking is recorded, not obeyed.** Acting on it would make the strategy
unbacktestable — different answer each run, and the model already knows how
these stories ended. The +0.218R holdout is the only real evidence here and it
depends on determinism. So both orderings go into the audit log and get
compared after a few hundred paper trades. If Gemma's `strong` calls have
outperformed its `weak` ones by then, promoting it is a measured decision.

It does not choose symbols, set entries, set stops, set targets, size
positions, or place orders. Those are arithmetic over price bars, and they are arithmetic on
purpose: the same bars always produce the same answer, which is what makes the
backtest meaningful and the orders auditable.

Three structural guarantees keep it that way:

- **Its output schema has no numeric fields.** Gemma cannot emit a price even
  if asked. Every number on screen is rendered from bars.
- **It runs in a different pipeline.** `scripts/run_scan.py` uses it;
  `find_setups.py`, `smc_backtest.py`, `dashboard.py` and `trade.py` never
  import it. If Ollama is not running, everything except news triage is
  unaffected.
- **Verdicts need a grounded, substantive citation** or they are dropped —
  copying the headline back does not count.

If the model layer ever earns a bigger role, it will be because a labelled
eval set measured it, not because it seemed to be doing well.

## Not investment advice

A personal research tool. Screens describe a stock's *current measurable
state*; they do not predict.

`scripts/trade.py` places real orders on a **paper** account — simulated money,
real market data. There is no live-trading flag in this repo. Pointing it at a
funded account is a decision to make deliberately, and the evidence behind the
strategy is currently ~200 trades from 86 days of data, with its own stop
threshold chosen by looking at those same trades.

## What to do next, in order

Updated 2026-09-28. Everything here is chosen because it either ADDS evidence or
SETTLES a question — not because it adds a rule. Twenty-three attempts at the
latter have failed.

**1. Collect 2021 to now.** Not for significance. For the question the model
sharpened and this project cannot currently answer: *does the strategy behave
consistently across regimes, or does it only look like it works in aggregate?*
Every test shows train negative and holdout positive, and the halves saw
different markets — 48% of train setups in elevated/stressed volatility against
24% of holdout. With 2022's nine-month bear market in the sample, that either
keeps flipping (a clean, final answer) or it holds (something real for the first
time). `MAX_STORED_BARS[30] = 5_000` caps the store at ~1.5 years and must be
raised first, or deeper history is silently trimmed.

**2. Fix the veto, or switch it off.** It fires on 20% of identical inputs at
temperature 0 and under-fires on the clearest case in its own prompt. Deriving
it from `conviction == weak` plus a named category would make it deterministic.
Until then `llm_veto_enabled = False` costs nothing that is actually working.

**3. Measure the fill rate.** Every backtest here assumes a GTC limit always
fills. Live it only fills if price returns to the trigger close, so setups that
gap away are counted and never entered. This is the largest unquantified gap
between the published number and a real one, and it can only make the number
worse.

**4. Score the slate.** `candidate_slate` and `position_review` records exist so
the model's calls can be checked against realised R. Three slates so far. This
is the only path to giving it more authority as a measurement rather than a bet.

**5. The short side.** Bearish sweeps are invisible to the screen rather than
rejected by it. It roughly doubles the sample, covers the regime where long-only
loses (−0.086R below both averages), and is the only thing that makes a
higher-timeframe bias a SELECTOR rather than one more filter. Real work: a
mirrored `detect()`, a `side` on `Setup`, an inverted `resolve()`, borrow costs,
and asymmetric gap risk.

**What not to do:** add filters. The search space has been sampled twenty-three
times with one near-survivor, and every further test now costs more in
multiple-comparisons correction than it can plausibly return.

## Roadmap

- [x] Point-in-time index constituents (removes the inclusion half of
      survivorship bias; see "Known biases" for the half that remains)
- [ ] Price history for delisted names — needs a source that keeps it, since
      Yahoo 404s and Stooq is behind a bot wall. Tiingo's free tier is the
      likely candidate (`SCANNER_TIINGO_API_KEY` already exists).
- [x] Transaction-cost model (spread, slippage, commission; breakeven bps)
- [x] Paper execution through Alpaca (bracket orders, risk sizing, guards)
- [x] Out-of-sample holdout split — chronological, floor re-chosen on train
      only; edge survives at +0.247R over 43 holdout trades
- [ ] Gap-fill modelling: stops are assumed to fill AT the stop price, and
      overnight gaps are exactly where they do not
- [x] Sleeve accounting — income and swing sized independently, positions
      attributed by `client_order_id`, dollar cap so profit can flow to income
- [x] Ex-dividend calendar and stop-erosion gate (a correction to
      dividend-adjusted backtest bars, not a predictive filter)
- [x] Income screen ranked on payment record rather than yield
- [x] One policy object feeding every entry point, pinned by a test
- [ ] **Verify the paper account actually credits dividends.** The `DIV`
      activity endpoint works and is empty; until a payer is held through an
      ex-date, the income sleeve is untested. This gates the rest of it.
- [x] Write outcomes back to the audit log — `trade_outcome` records realized
      R and exit reason, linked by `client_order_id`.
- [x] Shadow dimensions answered, and **none survives**. They were being
      measured on 99 decisions when each is arithmetic over bars already held;
      `shadow_test.py` evaluates them over 191 historical setups instead.
      Weekday p=0.517, liquidity tier p=0.798, session slot groups too small.
      The RSI gate could not be chosen on train at all and its holdout gap is
      confounded with time — 13 of 15 failures fall in two months.
- [x] Record the candidates that were NOT bought (`candidate_slate`). Without
      it the model's ranking is unfalsifiable by any amount of live trading:
      its third-choice calls never become orders, so they never become
      evidence. `replay_slate.py` scores them against bars already held.
- [x] Persist the headlines the model was shown (`news_store`). `fetched_at`
      was documented as a point-in-time stamp for backtests and nothing wrote
      one to disk, so a prompt change could not be tested against the past.
      Written from 2026-09-25 forward; nothing recovers what was discarded.
- [ ] Retrace entry as a backtest variant — enter on a return to the order
      block rather than at the trigger-bar close. Train split first, holdout
      once. The only untested idea likely to move the number.
- [x] Scheduled runs during market hours (05:33-12:03 AK) + off-hours data
      collection; evaluation continues when the market is closed
- [x] Live protection display — `positions.py` reads protective legs from the
      BROKER, not from the audit intent, and says so loudly when a position
      has none
- [ ] Daily P&L reconciliation
- [ ] Gemma 3 reads full 8-K text, not just SEC item codes
- [x] Roundup detector for multi-company headlines
- [x] Web dashboard (L5) — sector-grouped relative-strength heatmap
- [ ] Run the backtest over the full 1500 rather than the 500 (all three change
      logs now pass the drift audit; small caps make the missing transaction-cost
      model bite much harder, so cost modelling should land first)
- [ ] Labelled eval set for the triage layer (role + materiality), so prompt
      and model changes can be measured instead of eyeballed
- [ ] Parquet/DuckDB storage replacing the JSON cache
