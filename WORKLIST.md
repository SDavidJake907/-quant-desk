# Worklist — from the 2026-08-18 review

Ordered as the reviewer proposed. That ordering is better than the one in
`HANDOFF.md` §10 for one reason: it puts **freezing the policy** first, and
without a frozen window every trade collected afterwards belongs to a different
experiment and none of them pool.

Status key: `[ ]` open · `[x]` done · `[~]` partly done

---

## Done during the review

- [x] **Verify the scan boundaries.** Both were wrong in one direction and
      right in the other.
  - After-close is **sound**: 12:03 AK = 16:03 ET, Alpaca's `/v2/clock` reports
    shut, the account gate forces `dry_run`. Confirmed in the 08-17 log.
  - Open was **broken**: `resample` returned the bucket still forming, so the
    newest bar in a live scan was three minutes old with 27 minutes left to
    change. The strategy enters at the *close* of the trigger bar; a partial
    bar has no close. Worst at 05:33 AK (09:33 ET) where the partial bar is the
    entire session. Fixed by `drop_incomplete`, live path only. Commit
    `f19aeff`.

---

## 1. Freeze the policy — do this before anything else

**DONE — see `FREEZE.md`.** Window opened 2026-08-18 at policy
`9fe7a6618823`, build `9ea2d7d`. Stop rule: 100 completed scanner trades AND
12 calendar weeks, checkpoint at 30 for reporting only, no optional stopping.

- [x] Stop changing `TradingPolicy`. Fingerprint moved **three times today**
      (`3195873fc5b6` → `cfd459a03db3` → `dcdf791c3035` → `9fe7a6618823`), so
      the trades already collected cannot be pooled.
- [x] Declare an evaluation window with **both** a minimum trade count and a
      minimum calendar duration. At the observed ~0.6 orders/day, 100 completed
      trades is roughly **eight months** — stated in `FREEZE.md` because it is
      uncomfortable and needs to be seen before it is agreed to.
- [ ] Report results **grouped by `(policy, build)`**. `positions.py` pools
      everything and mixes in the five manual trades. Needed before the
      30-trade checkpoint.
- [x] Frozen fingerprint, build and window rules written to `FREEZE.md`.

**Note:** the partial-bar fix above changes behaviour but not the fingerprint —
it is a data-layer correctness fix, like the adjusted-price bug. Freeze *after*
it lands, which is now.

## 2. Audit records must carry outcomes

- [ ] Write realized R and exit reason back to `logs/trading_audit.jsonl`. It
      records what was decided and never what happened; realized R is recomputed
      on demand from Alpaca's order history, which is not permanent.
- [x] Every audit record now carries the policy fingerprint **and a build SHA**
      (`+dirty` when the tree is edited). A fingerprint alone cannot separate
      trades made before and after a data or execution fix — the partial-bar
      change proved that.
- [ ] Append a `trade_outcome` event linked by a permanent `trade_id`. **Never
      rewrite an earlier JSONL record**; the log is append-only and its value
      is that it cannot be revised after the fact.
- [x] **The shadow dimensions were redeemable all along — offline.** They
      were being written onto DECISIONS, of which there are 99, and
      `rsi_gate_passed` is True on 97. Comparing a 98% group to a 2% group
      needs thousands of trades; at 0.3 a day that is never. But each one is
      arithmetic over bars already held, so `scripts/shadow_test.py` computes
      them for 191 historical setups instead.

      **Nothing survives.** Split chosen on train, applied unchanged to
      holdout, four questions asked so the threshold is 0.05/4 = 0.013:

          weekday          Fri vs Wed        -0.359R  p=0.517
          liquidity_tier   over-50M vs 20-50M +0.142R  p=0.798
          session_slot     holdout groups too small to test

      `rsi_gate` could not be chosen on train at all — only 2 False cases —
      and its holdout gap (+0.533R on 51 vs -0.562R on 13) is **confounded
      with time**: 13 of its 15 False cases fall in August and September, so
      the split is mostly "recent" versus "not recent". It is unresolvable
      from this sample rather than positive or negative, and the tempting
      1.1R gap is exactly the kind of number this project has been burned by.

      LLM ranking stays genuinely unanswerable from history: it reads
      headlines, and nothing persisted them before 2026-09-25.

- [ ] Re-run `shadow_test.py` as the store deepens. Three dimensions are
      null on 191 setups, which is not the same as null.

## 3. Storage and compute — REMEASURED 2026-09-25, the old note was wrong

The original note said reading 500 cached symbols took **104s** against **9s**
of compute, concluded I/O was 12x compute, and that a GPU would not help.

Remeasured against the ACCUMULATED STORE, which is what research actually
reads now:

        I/O  (read store + parse)   0.93s    4%
        CPU  (scan/screen)         20.27s   96%

It is the other way round. The old figure was measuring the raw Yahoo payload
cache (`data/bars`, full JSON chart responses); `history.accumulated` reads a
compact row store and is 20x faster. Both numbers were true of different
things, and the conclusion drawn from the wrong one shaped this section.

### A GPU is still the wrong tool, for a better reason

`screens/smc.py` is **pure Python** — no numpy anywhere. Tuples of floats,
sequential loops, data-dependent branching, and a per-symbol series of about
1,200 bars. CUDA would need the screen rewritten as array operations first,
and then arrays that small would be swamped by kernel launch overhead. The
pattern logic is also inherently sequential: structure is built from prior
structure.

### The actual win is the nineteen idle cores

The work is embarrassingly parallel ACROSS symbols, and this machine runs 20
cores while using one:

        serial     21.27s   206 setups
        parallel    2.11s   206 setups   (ProcessPoolExecutor, 20 workers)
        speedup     10.1x   identical result

- [ ] Parallelise the research scripts across cores. Ten minutes of work for
      10x on every backtest, ablation and permutation test. The LIVE scan
      would gain little — its 22s is mostly network fetch, not compute.
- [ ] Parquet/DuckDB is still worth doing for a deeper store, but it is no
      longer the bottleneck and should not be sold as one.

## 4. Survivorship — delisted price history

- [ ] Tiingo free tier; `SCANNER_TIINGO_API_KEY` already exists in config.
- [ ] Gates the momentum result: **80 S&P 500 members have no price history**
      and deletions skew toward companies that failed, so every long result
      currently reads high.

## 5. Momentum factor — costs and valid inference

- [x] **HAC / Newey–West standard errors.** Done 2026-10-04,
      `momentum_hac_test.py`. 10y, 63d hold, 21d step, top 10, point-in-time:
      +2.68% excess per hold (random control -0.51%); clustered t 2.48 ->
      **Newey-West t 1.89** (lag 2; 1.87/1.88 at lags 3/5). 5y: +3.74%, NW t
      1.36. Positive at every lag and cost, beats its control, but under |t|=2.
- [x] **Costs.** 50bps round trip costs ~0.5% per 63-day hold: 10y +2.18%,
      NW t 1.54. Costs are NOT what limits this; significance is. The
      strongest candidate for a second engine, on outside evidence as much as
      this repo's -- a lead, not a finding.
      Original item, for its reasoning:
      **HAC / Newey–West standard errors.** A 63-day hold with 21-day steps
      makes consecutive rebalances overlap by two-thirds. The engine's
      "clustered" t of **+2.42** treats rebalances as independent and overstates
      confidence. Use a lag of at least `horizon/step − 1` (= 2), or block
      bootstrap.
- [x] Re-test after costs (see above: 50bps leaves +2.18%/hold). Published large-cap momentum implementation runs
      **2–4% annually** against ~7% gross excess — potentially half the result.
- [x] Only after both: decide whether the excess survives. It survives costs;
      it does not clear |t|=2 after the overlap correction (1.89). A lead for a
      second engine in its own window, not a proven edge.

## 6. Momentum as a separate engine — not grafted onto trade.py

- [ ] Keep the two strategies apart. They differ in every mechanic:

      | SMC swing | momentum factor |
      |---|---|
      | event-driven entry | scheduled rebalance |
      | stop/target bracket | portfolio-level exit |
      | sizing from stop distance | sizing from portfolio weight |
      | needs a fresh setup | needs relative rank |

- [x] Monthly rebalance loop, equal weight, no stops. `sizing.py` cannot size
      it — with no stop there is no input.
      Built 2026-10-05 (c77796b): `src/scanner/momentum/` + 
      `scripts/momentum_rebalance.py`, DRY RUN ONLY. Design = §31 arm A,
      universe S&P 500 + MidCap 400 (operator's choice). First dry-run
      tranche recorded 2026-10-06: 8 of 10 picks Information Technology.
- [ ] Before live: fractional shares or a larger book -- at $100k a tranche
      puts ~$3,333 per name, so SNDK at $1,720 gets 1 share (48% under).
- [ ] Before live: market-on-close submission with a `mom-` prefix, its own
      sleeve, kill switch, FREEZE.md entry; dry run November first.
- [ ] Decide (and TEST first) whether to cap one sector; today 8/10 is one
      theme, which the crash test did not model.

## 6b. Friday flatten safety — mostly done

- [x] Cancel every unfilled entry order before closing positions
- [x] Cancel bracket legs first — a resting leg reserves the shares, so a close
      underneath one can be rejected for insufficient quantity
- [x] Query broker quantities; never trust local state
- [x] Close with `DELETE /v2/positions/{symbol}` so quantities are the broker's
- [x] Idempotent — a second `--submit` cannot open a short (pinned by test,
      including the short-position case)
- [x] Poll until positions **and** working orders are both zero, 45s budget
- [x] Friday entries blocked by `wind_down_from_weekday`, independently of this
      job, so a missed flatten cannot re-enable them
- [x] Alpaca market calendar for holidays and early closes — two triggers
      (08:45 + 11:45 AK), each acts only if the bell is within 25 minutes
- [x] Loud alert and non-zero exit if not flat
- [ ] Record realized R, fill price and remaining quantity in the
      `weekend_flatten` record (currently records counts and what is stuck)
- [ ] **Watch the first live run**, Friday 2026-08-21. First time this code
      sends closing orders on a schedule.

## 7. News gate — decide the failure direction before live

- [ ] Currently **fails open**: with Ollama down every name passes the news
      check unrefused, including takeover targets and structural-news names.
      Now loudly logged (`news_gate_degraded`) but still permissive.
- [ ] Acceptable for paper — the point is to observe behaviour. **Not
      acceptable live.** Before any real funds, either:
      - fail closed for new entries when the model is unreachable, or
      - explicitly disable the gate and record that the system runs without
        news protection.
- [ ] Make it a `TradingPolicy` field so the choice is in the fingerprint.

## 8. `fresh_bars = 2` — investigated, and the first answer was WRONG

**Leave the gate alone. But not for the reason first published.**

### The first result was contaminated, and this is the correction

The first pass re-entered each setup N bars late at that bar's close and kept
the original stop — and never re-checked the policy at the price actually
paid. `scan` enforces `min_risk_pct` and `min_reward_risk` at the TRIGGER
price; a delayed entry moves the price, leaves the stop, and both can quietly
stop holding.

As a delayed entry drifts toward its stop the risk denominator shrinks toward
zero and R explodes. LITE resolved at **-135R on a $0.33 risk** against an
$866 stock; HOOD at **+82R on $0.07**. Those are not trades that went badly
or well — they are trades the live scanner would REFUSE, and averaging them
measured a strategy nobody runs. One of them moved a holdout column by more
than a full R.

That produced a clean monotonic decay and p-values of 0.005. It was an
artifact. Re-applying `min_risk_pct` and `min_reward_risk` at the delayed
entry, on the full ~96-session store:

        delay      holdout      takeable
        0 bars *   +0.248R       94%
        1 bar  *   +0.300R       45%
        2 bars *   +0.190R       40%
        3 bars     +0.420R       37%
        5 bars     +0.208R       38%
        8 bars     +0.319R       32%
        13 bars    -0.358R       18%
        20 bars    -0.322R       14%

        paired randomisation, 0 vs stale
        TRAIN    8 bars p=0.419 · 13 p=0.836 · 20 p=0.633
        HOLDOUT  8 bars p=0.731 · 13 p=0.046 · 20 p=0.755

**Delay does not significantly hurt expectancy out to ~8 bars.** Five of six
paired tests are null, and the one that is not has n=12. The earlier claim
that widening would buy negative-expectancy trades is withdrawn.

### Why the gate still should not move

`takeable` is the answer, and it was the column nobody was looking at.
**A single bar of delay disqualifies more than half of all setups** — 94% to
45%. Price moves one bar, the stop stays, and either the risk falls under the
1.5% floor or the reward:risk falls under 1.5. Both directions disqualify:
drift down shrinks the risk, drift up shrinks the reward.

So the freshness gate and the policy's own risk floor are largely redundant.
Widening `fresh_bars` would not release the flood of trades the scarcity
numbers imply, because most of those 8,455 stale setups would fail the risk
floor at today's price regardless. The gate is not what is starving this
strategy — the entry geometry is.

- [x] Outcomes by age bucket
- [x] Count surviving each successive gate — and this turned out to be the
      finding rather than a supporting detail
- [ ] Setup age histogram — still not needed
- [ ] **Re-test with a re-derived stop.** Every delayed entry here keeps the
      original stop, which is what makes it stop qualifying. A setup re-sized
      from its structure at the later price might stay valid and stay
      profitable. That is a different experiment and the honest next one.

### What has NOT changed

Holdout expectancy at delay 0 is **+0.248R** on 61 setups, against the quoted
**+0.218R**. Closer than the contaminated 60-day pass suggested (+0.141R), and
no longer evidence that the edge is thinner than advertised. Different sample,
still not subtractable, but the earlier worry is withdrawn too.

**No policy field changes.** The gate stays at 2 because nothing here is
evidence for moving it, not because moving it was proven harmful.

My first theory here — that detection lagged the trigger — was measured and
proved wrong: all 133 setups are visible at age 0. Do not re-derive it.

## 11. Excursions — the winners barely survived

`scripts/excursions.py` resolves each completed trade against stored bars and
tracks how far it ran against the entry (MAE) and in its favour (MFE), both in
R. Borrowed from `core/risk/exits.py` in the LLM-Quant-Engine project on this
machine — the mechanism, not its numbers, which are crypto.

        sym      result      MAE      MFE  reason
        CCL      -0.939   -1.094   +0.772  stop
        EXPE     +3.197   -0.532   +3.159  target
        IP       -1.122   -1.072   +0.209  stop
        CEG      +2.315   -0.417   +2.323  target
        Q        +1.721   -0.882   +1.733  target
        AMD      -0.683   -1.571   +0.559  stop
        SUPN     -1.043   -1.004   +0.368  flattened
        APP      -0.363   -1.054   +0.105  stop
        ACN      +2.163   -0.962   +2.360  target
        PSX      -0.471   -1.258   +0.000  stop

**Two of the four winners came within 0.2R of being stopped out.** ACN reached
**-0.962R** before finishing +2.163R, and Q reached -0.882R before finishing
+1.721R. Those two carry much of the positive expectancy, and they survived on
margins a worse fill erases.

### The fill gap, visible in one column

MAE is computed from the INTENDED entry; realized R comes from the ACTUAL
fill. So the gap between them IS the fill advantage:

        AMD   bars say -1.571R    account says -0.683R
        PSX   bars say -1.258R    account says -0.471R
        APP   bars say -1.054R    account says -0.363R

Nearly a full R on AMD. Every loser filled better than intended, which is the
same +0.172R paper optimism replay found, now itemised. Real money does not
get those entries.

- [ ] Re-read expectancy with this in mind. The mean is +0.477R, drops to
      +0.175R without the single best trade, and rests on two winners that
      nearly stopped out at fills the account would not have got.
- [ ] No loser ever reached +1R, so these are stop-placement outcomes rather
      than exit-rule ones. That is the one encouraging line here.

n=10. This describes what happened; it is not a finding.

## 12. Multi-horizon context — tested 2026-09-25, nothing survives

The idea: a setup triggering after a month of decline is a different thing
from one triggering in an uptrend, and the screen does not care. Hand the
model 24h / 7d / 14d / 30d context so it can judge news against price.

`ranking.py` deliberately refuses to — "you are NOT judging the chart, price
is not your job" — so the boundary was tested rather than crossed.
`scripts/horizon_test.py` labels every historical setup by percentage change
into the trigger bar over 13 / 65 / 130 / 273 bars, bucketed into bands fixed
before looking, and scores them against realized R.

        HOLDOUT, split chosen on train, threshold 0.05/4 = 0.013

        1d    up vs flat    -0.165R   p=0.674
        7d    down vs flat  -0.298R   p=0.455
        14d   holdout bands too small (24 vs 5)
        30d   holdout bands too small (22 vs 7)

**Both testable horizons flipped sign between train and holdout.** Train
picked "up" as the best 1d band and "down" as the best 7d band; on the
holdout each was WORSE than flat. That is the same signature as the RSI
divergence result in the README — an effect that reverses across halves is
noise, and reading either half alone would have produced a confident and
wrong answer.

This is the tenth context test in this project and the tenth null. The
running tally: 17 signal filters, 7 of 9 context gates worse, 13 ablation
variants, 4 shadow dimensions, delayed entry, and now four horizons.

- [ ] Re-run as the store deepens. 14d and 30d could not be tested at all —
      their holdout bands held 5 and 7 setups. Null on ~190 setups is not
      null.
- [ ] Recording horizon state on every decision still costs nothing and
      would let this be scored properly later. Showing it to the model has
      no evidence behind it and would cross a boundary that exists for a
      measured reason.

## 13. Zero-volume placeholder bars are shrinking the universe

`scripts/verify_bars.py` asserts the invariants nobody checks because they are
obviously true. The bars pass all of them — OHLC consistent, times strictly
increasing, buckets aligned to :00 and :30, nothing outside 09:30-16:00 — and
every setup the screen returns honours the policy it was scanned under.

One thing does not pass. **12.7% of session-days hold 14 bars, and a
09:30-16:00 session is 13.**

The extra bucket is 16:00, and it is not a bar:

        bucket   median volume   median range
        15:30        2,665,138          1.238
        16:00                0          0.000

Zero volume, zero range, high equal to low. A Yahoo placeholder for the
closing print. 2,591 of 250,742 bars, 1.03%.

### What it costs

Nothing triggers on one — 0 of 92 setups — but the liquidity filter averages
volume x close over the last 20 bars, and a zero drags the mean down:

        median dollar volume as stored     19,890,206
        median with placeholders removed   21,275,399
        understated by                          6.51%

The floor is 20,000,000, so the median S&P 500 name sits **right on the
boundary and the artifact pushes it to the wrong side**:

        symbols passing, as stored                245
        symbols passing, placeholders removed     267
        wrongly rejected                           22   (+9.0%)

        KEY   19,847,701 -> 22,728,781
        FITB  19,547,935 -> 22,025,013
        VRTX  19,834,307 -> 21,315,726
        CL    19,097,699 -> 21,180,890

Twenty-two genuinely liquid large caps excluded by a data artifact. For
comparison, adding the entire S&P 400 and 600 — 1,003 extra symbols — bought
only +0.15 setups per session. This is free and comparable.

### Two possible fixes, and they are not equivalent

- [ ] **Narrow:** exclude zero-volume bars from `_dollar_volume` only. Fixes
      the measured defect, touches `trade.py` and `find_setups.py`, and
      leaves structure detection exactly as it is — so no historical result
      moves.
- [ ] **Broad:** drop placeholder bars at parse time so they never enter a
      series. More correct — a bar with no trading is not a bar, the same
      reasoning `drop_incomplete` already applies to a bucket that has not
      closed. But **13% of setups have a placeholder inside their structure
      window**, so this can change which setups exist, and every historical
      number including the +0.218R baseline would need re-measuring.

The narrow fix is quantified and safe. The broad fix is more correct and
needs its own before/after study before anyone trusts it. Do the narrow one
first, measure the broad one separately.

## 14. Stop-to-noise — the first ranking idea that did not flip

Eleven ranking ideas have died here, several by reversing between halves.
The twelfth, run 2026-09-25, is the first that did not.

### First, the eleventh, because it is the more striking result

`equal_lows_test.py` asks whether sweeping a POOL of equal lows beats
sweeping an isolated one. That is not a borrowed indicator — it is **this
strategy's own central thesis**, that stops stack under a level which has
held twice:

        TRAIN     pool minus isolated   +0.616R
        HOLDOUT   pool minus isolated   -0.674R

A complete reversal. The thesis the screen is built on does not survive its
own holdout, on 196 setups.

### The twelfth: stop distance measured against the name's own noise

`min_risk_pct = 1.5` applies one number to every symbol. On a quiet name a
1.5% stop sits outside normal movement, so being hit means the structure
failed. On a name whose bars routinely travel 3%, the same stop sits INSIDE
one bar's ordinary range and gets taken out by drift.

So the measure is the ratio, not the level: stop distance / ATR(14) at the
trigger. Bands fixed before looking.

        TRAIN                      HOLDOUT
        1-2x ATR  +0.544R n=58     1-2x ATR  +1.076R n=11
        2-3x ATR  +0.349R n=55     2-3x ATR  -0.072R n=36
        3x+  ATR  +0.864R n=15     3x+  ATR  +0.134R n=19

        train picked 3x+ over 2-3x   gap +0.515R
        holdout                      gap +0.205R   p=0.604

**Same direction, not significant.** p=0.604 is nothing and n=19/36 is thin.
But it did not reverse, which eleven others did, and that is the only
distinction available at this sample size.

### Why this shape of idea and not another indicator

The one thing that ever worked here was the 20M liquidity floor — +0.114R
below against +0.776R above, p=0.022. It is not a prediction. It says where
the strategy's MECHANISM applies: this trades liquidity sweeps, a sweep needs
resting orders, a thin name has none.

Stop-to-noise asks the same kind of question about a different mechanism —
not "is this setup good" but "does a 1.5% stop mean anything in a name that
moves 3% a bar". Every idea that failed was a claim about a setup. The one
that worked was a constraint on the universe.

- [ ] Re-run as the store deepens. Holdout bands of 19 and 36 cannot settle
      this either way.
- [ ] Do NOT act on it yet. p=0.604 is not evidence, and acting on a
      not-yet-null is how the thirteen-variant mistake happened.

## 15. Sweep rejection shape — the code is not missing it

`took_out` decides a sweep happened on one comparison — `low < prior.price`.
Only the LOW. Nothing about where the bar closed. So a bar that stabs below
the level on a long wick and closes back near its high scores identically to
one that closes on its low, and those are opposite pictures to anyone reading
a chart: the first is a rejection, the second is a breakdown.

That is a real blind spot, and it is not an indicator — it is the shape of a
single candle, and it is the shape the strategy's own thesis rests on. So it
was measured. Close position within the sweeping bar's own range, thirds
fixed before looking:

        shape mix: closed weak 57, closed mid 54, closed strong 85
        median close position: 0.59 of range

        TRAIN                        HOLDOUT
        closed weak   +0.459R n=37   closed weak   +0.462R n=20
        closed mid    +0.978R n=41   closed mid    +0.221R n=13
        closed strong +0.226R n=52   closed strong -0.009R n=33

        train picked mid over strong   gap +0.751R
        holdout                        gap +0.230R   p=0.646

Same direction, not significant. But read the bands rather than the verdict.

**"Closed strong" — the textbook rejection — is the WORST band in both
halves.** +0.226R on train, -0.009R on holdout. The shape a discretionary
trader would call the cleanest signal carries no advantage and possibly a
disadvantage.

And 57 of 196 sweeps closed in the bottom third of their range. Those are
breakdowns a human would refuse to trade, and they were the most consistent
band across halves: +0.459R and +0.462R.

### What this settles

The intuition was sound — the code genuinely cannot see rejection, and that
gap is real. The answer is that the gap costs nothing, because the visual
quality does not predict the outcome. Adding a rejection requirement would
have removed the steadiest band and kept the weakest.

That is now thirteen tests. Twelve were indicators; this one was chart shape,
which is a different kind of claim, and it landed in the same place. Together
with the equal-lows reversal — the strategy's own central thesis, +0.616R on
train and -0.674R on holdout — the pattern is that what LOOKS like quality in
this setup is not quality.

- [ ] Re-run as the store deepens. Holdout bands of 13 and 33 settle nothing.
- [ ] Do NOT add a rejection filter. It would keep the worst band.

## 16. Filter audit — two drift bugs and a free gate, fixed 2026-09-26

Asked for a precision pass over every filter. Full gate sequence, in order:

```
BAR      drop_incomplete · placeholder rejection (zero-volume 16:00)
SYMBOL   min_price 5.0 · min_dollar_volume 20M · min_range_pct 0.0 (off)
SETUP    swings k=2 · sweep · higher_low · CHoCH · BOS
         stop<entry<target · min_risk_pct 1.5 · min_reward_risk 1.5
         require_value_sweep · dedup on (sweep, higher_low)
FUNNEL   is_live_setup · fresh_bars<=2 · crosses_session
         skip_open · wind_down_from_weekday
ORDER    already-held · room · size_position · ex-dividend · check_news
ACCOUNT  kill switch · market_open · check_account · unprotected-position warn
```

`verify_bars.py` already proves every setup honours the risk and R:R floors.

**Fixed — `min_price` drift, the same bug twice.** `policy.min_price = 5.0`
existed and was read by nobody; `trade.py`, `find_setups.py`,
`collect_news.py` and `provider_compare.py` each hardcoded their own copy. The
comment documenting the *identical* fix for `min_dollar_volume` was written
directly below the hardcoded line in `trade.py`. Worse than the original:
`min_price` is a policy field, so it is fingerprinted — editing it would have
declared a fresh evaluation window and changed nothing live. A false signal,
not a missed one.

**Fixed — `MIN_RANGE_PCT` enforced only in the preview.** `find_setups.py`
alone defined and applied a 0.4% average-range floor; `trade.py:_liquid()` had
no range check. Measured: the preview showed **230** symbols where the trader
acts on **267**, hiding MA, KO, PG, BRK.B and 33 others. WBD averages 0.088%,
where a 1.5% stop is seventeen bars of ordinary movement.

Now `policy.min_range_pct`, read by both, fingerprinted, **defaulted to 0.0 —
declared and off**. The mechanism argument for enabling it is the same one that
carried the 20M floor, but that floor was measured *before* enforcement
(p=0.022, 125 setups, same sign both halves) and this has never been measured
on any split. Enabling it drops 14% of the universe on an untested hypothesis
and orphans the +0.218R holdout, which was measured with no range filter.

**Fixed — the context gate computed indicators for a foregone answer.**
`intraday_context.passes()` has eight `require_*` flags, all defaulting False,
none set by the live policy. It computed four EMAs, a Wilder RSI, a MACD, an
ATR and a volume ratio, then returned a `True` no input could change. Now
short-circuits. The flags stay — they are how the next constraint gets tested.

**Result:** universe 267, 197 setups, 833 tests green, live path clean.
Fingerprint `5b091652a8ab` -> `29378de4d359` because a field was *added*, not
because a rule changed. Window 8 had no trades, so nothing is lost.

### Still missing, and worth a measurement

- **No spread filter anywhere.** Nothing reads bid/ask. `excursions.py` showed
  fills dominate — two winners survived within 0.2R of the stop — and Alpaca
  SIP now provides quotes. A tradeability CONSTRAINT, the only category that
  has ever worked here.
- **No gap-risk filter.** Roughly half of these positions are held overnight
  by design. A name that gaps often is one where the stop is bypassed rather
  than hit, and losers already fill nearly a full R worse than intended.
- **No upper price bound**, and `min_range_pct` untested. Both cheap to split.

### Next
Measure `min_range_pct` as a band study on the train/holdout split before
considering 0.4. Treat the prior as null: thirteen ideas have failed that bar.

---

## 17. The system has no concept of bull or bear — measured 2026-09-26

Asked whether the repo understands market direction. It does not, anywhere in
the trading path, and the sample it was validated on could not have revealed
the omission.

### What is actually wired

`scheduled_scan.bat` runs `trade.py --submit-paper`. No index filter, no regime
gate, nothing market-wide. The ingredients exist and are all off:

- `intraday_context` computes `bullish_alignment`, `above_200`, `fast_stack`,
  reachable only through `--trend-context` etc. — `store_true`, default False,
  never passed by the scheduler. These are the same flags §16 short-circuited
  because nothing sets them.
- `_sector_standing()` does fetch `^GSPC` and compute relative strength, but it
  feeds the LLM ranking prompt only. It orders candidates; it never rejects one.
- `find_setups.py --bullish` and `smc_backtest.py --bullish` exist.
  `trade.py` has no equivalent.

Structurally long-only: in `screens/` and `execution/`, `"sell"` appears only
in exits and the Friday flatten, never as an entry side. A bearish sweep is not
filtered out — it cannot be represented.

### The validated sample contains no down-trending session

S&P classified against its own 50- and 200-day averages:

```
Yahoo store, 2026-05-14 -> 2026-09-24   (S&P +2.7%)
  above both    80 of 92 sessions   87%
  mixed         12 of 92 sessions   13%
  below both     0 of 92 sessions    0%
```

Six docstrings here say "one market regime". It is more specific than that: one
FAVOURABLE regime. The +0.218R holdout, the 20M floor at p=0.022 and the
thirteen null rankings were all measured inside it.

### On data that does contain a downturn

The Alpaca SIP store spans 2024-09 -> 2026-09 and covers the 2025 -18.9%
drawdown. Same rules, 501 symbols (collection complete: 0 failed, 3,343,967
bars, 180 MB), 858 resolved setups:

```
above both    +0.108R  se 0.078  n=523  win 38%
mixed         +0.028R  se 0.119  n=156  win 38%
below both    -0.086R  se 0.125  n=179  win 32%

gap (above - below)  +0.194R   permutation p=0.201
```

Bear conditions go negative, the direction theory predicts for a long-only
sweep strategy. NOT A FINDING — p=0.201. But refreshed on the completed
501-symbol store (858 setups, up from 565 on 336 symbols) the `below both`
bucket held almost exactly: -0.089R on n=114 -> -0.086R on n=179, while `mixed`
swung +0.108R -> +0.028R and showed itself to be noise. Stability under a 57%
larger sample is what separates an effect from an artifact, and the ordering is
now monotonic. This is the most promising unenforced result in the repo after
the 20M floor, and it still needs a third sample before enforcement.

### The larger thing this surfaced: the window, not the provider

"above both" reading +0.040R rather than +0.31R prompted the matched provider
comparison on the shared dates:

```
2026-05-11 -> 2026-09-25      YAHOO    ALPACA SIP
  mean R                     +0.380        +0.311
  se                          0.150         0.142     agree within 1 se
```

So the edge is real and is not Yahoo's reconstruction of the tape — the first
replication this project has had. Which means the gap between **+0.311R over
4.5 months** and **~+0.025R over 2 years** is a WINDOW effect. The period every
published number came from was unusually favourable, and it is the same period
`min_risk_pct` was chosen on.

That qualifies the headline result more sharply than the regime question did.

### Next, in order

1. Re-collect Alpaca from `--start 2016-01-01` instead of 2024-09. Reaches four
   more drawdowns — 2018 (-19.8%), 2020 (-33.9%), 2022 (-25.4%, nine months),
   2025 (-18.9%) — and takes the sample from 0.38 years to ~10. Also the fix
   for needing 224 trades to exclude zero and for the 0.82 deflated Sharpe.
   One command, free.
2. Measure a `below both` exclusion the way the 20M floor was measured:
   train/holdout, permutation, sign-flip check, BEFORE enforcing anything.
   Prior is better than the thirteen failed rankings, because like the
   liquidity floor it is a claim about where the MECHANISM applies rather than
   a prediction — a long-only sweep needs buyers to defend the level.
3. Only then consider the short side, which is a much larger change: it is not
   a filter but a second direction the screen cannot currently express.

---

## 18. Timeframe tested; 30m stays. And win rate is an inverse proxy

Asked what bar size is best for entry and exit. Five timeframes on the
501-symbol Alpaca store, `scripts/timeframe_test.py`.

Two confounds were neutralised first, either of which would have decided the
answer on its own:

- `max_hold_bars = 60` is counted in BARS: 30 trading hours at 30m (~4.6
  sessions) but 240 hours at 240m (~37 sessions). Held constant at 1800
  MINUTES instead, so every series gets the same wall clock.
- A 390-minute session is 13 x 30 and not a whole multiple of 60/90/120/240.
  Buckets are chunked WITHIN a session, never across one, so a session's last
  bar is short rather than spanning the overnight gap.

`min_risk_pct`, `min_reward_risk` and `swing_k` unchanged, or the comparison
stops being about the bar.

```
   bar   setups   hold        ALL      TRAIN    HOLDOUT     win
   30m      860    60b     +0.054     -0.096     +0.352   36.9%   <- live
   60m      825    30b     +0.058     +0.041     +0.092   41.8%
   90m      715    20b     +0.042     +0.047     +0.033   43.2%
  120m      698    15b     -0.004     +0.018     -0.047   44.0%
  240m      424     7b     -0.020     +0.007     -0.075   46.5%

vs live 30m, holdout only
    60m  -0.261R  p=0.066   not distinguishable
    90m  -0.319R  p=0.028   worse
   120m  -0.400R  p=0.006   worse
   240m  -0.428R  p=0.014   worse
```

**Coarser than 60m is genuinely worse** — monotonic in bar size and significant
at 90m, 120m and 240m. Mechanically sensible: a sweep is an event at a level,
and a 4-hour candle smears the sweep and the reaction into one bar, so
`took_out` cannot separate them.

**But 30m is NOT shown to beat 60m.** On ALL setups they are a dead heat
(+0.054 vs +0.058). The holdout comparison flatters 30m because 30m's halves
disagree violently — train -0.096R against holdout +0.352R, a swing of
+0.448R — while 60m is stable (+0.041 -> +0.092). A -0.261R gap at p=0.066
resting on one anomalously strong half is not actionable.

**Decision: no change.** 30m stays because nothing beat it, not because it won.

Five trials, declared. The eight `require_*` context flags were deliberately
NOT crossed with this: each was already tested individually and each was null,
and a 5 x 8 grid adds forty trials to a deflated Sharpe already at 0.82
against 50.

### The result worth more than the timeframe answer

Win rate climbs monotonically with bar size — 36.9% to 46.5% — while
expectancy falls. That is the THIRD independent demonstration of the same
trade-off today:

```
intervention                      win rate          expectancy
nearest structural target     37.0% -> 85.4%   +0.056R -> -0.013R
structural lower-low exit     41.5% -> 48.1%   +0.358R -> +0.273R
coarser bar (240m)            36.9% -> 46.5%   +0.054R -> -0.020R
```

Three unrelated changes — moving the target, adding an exit rule, resizing the
bar — every one raised win rate and every one cost expectancy. That is a
structural property, not a coincidence: the money sits in the ~25% of trades
that reach a far structural target and average +2.383R, and anything making
wins more frequent truncates exactly those.

**So win rate is close to a pure inverse proxy for expectancy here, and should
not be optimised.** Worth remembering before the next idea that improves it.

---

## 19. The universe is not in the fingerprint — APPLY AT THE NEXT BOUNDARY

`TradingPolicy` carries 29 fields, from `min_price` to `shadow_llm_ranking`.
The UNIVERSE is not one of them. It is a CLI argument:

```python
p.add_argument("--index", choices=list(INDEXES), default="sp500")
```

So `--index all` instead of `--index sp500` changes which symbols are traded —
267 names becomes 287 — and the fingerprint stays `29378de4d359`. Two windows
with identical fingerprints could have scanned different universes and nothing
in the audit log would tell them apart.

This is the `min_price` defect from section 16, INVERTED AND WORSE. `min_price`
moved the fingerprint without changing behaviour: a false alarm, annoying.
The universe changes behaviour without moving the fingerprint: a silent change.
FREEZE's own table promises "a policy rule -> moves -> caught by fingerprint",
and this rule is not caught.

`scheduled_scan.bat` passes no `--index`, so live runs use the `sp500` default.
Nothing is currently wrong in the log. The hole is that nothing would show if it
were.

### Fix
Add the universe to `TradingPolicy` as a declared field and have `trade.py`
read it, the way `min_dollar_volume` and now `min_price` are read.

### WHY THIS IS NOT DONE YET — the boundary matters
Adding a policy field moves the fingerprint, which supersedes window 10. Every
previous supersession was free because those windows held zero trades. Window 10
has **MLM open right now**, and its provenance is already weakened by the
`7247e3f+dirty` build. Orphaning it into a closed window mid-position costs more
than waiting.

Apply when MLM resolves — stop, target, or Friday's flatten at the latest — and
make both decisions at that one boundary.

## 20. Market-cap expansion measured: it buys 20 symbols, not a better universe

Asked whether widening past the S&P 500 would find better setups. Measured
against the live floors (price >= $5, dollar volume >= 20M):

```
index    symbols  have bars  pass floor  % liquid   median $vol
sp500        501        501        267      53%     37,692,206
sp400        400        400         18       4%     38,197,493
sp600        603        602          2       0%     29,368,028

TOTAL       1504                   287
```

Going to all 1504 adds **20 tradeable names**, 267 -> 287, +7.5%. The 20M
liquidity floor already excludes 96% of mid caps and 99.7% of small caps, so the
market-cap question and the liquidity-floor question are THE SAME QUESTION.

The 20 that qualify are genuinely liquid — median $38M per bar, comparable to
the S&P 500 median — so there is no quality objection. The objection is price:
+7.5% evidence rate against 3x the scan (501 -> 1504 symbols, ~17s -> ~50s of
compute plus the fetch), and a window restart.

Note the asymmetry already present: `scheduled_data.bat` COLLECTS `--index all`
(1504 symbols) while the scan reads `sp500`. The data is already being gathered
and is already unused.

**Decision: not now.** If more setups are wanted, the lever is the liquidity
floor, not the index — and that floor is the one measured result here (+0.114R
below 20M against +0.776R above, p=0.022). Lowering it buys hundreds of names at
measurably worse expectancy. That trade has already been decided with evidence.

Revisit alongside section 19, once the universe is fingerprinted and the knob is
visible in the audit trail.

## 21. Two smaller things left open 2026-09-28

**The health probe overstates itself.** `execution/health.py:probe()` calls only
`/api/tags`, a disk listing. "news gate OK — gemma3:12b reachable" prints
identically for a model that is present but cannot load — OOM, corrupt blob, GPU
contention — and the failure would surface only when the first real candidate
arrived, inside the 25-minute task limit. One tiny inference would make OK mean
the model runs.

**`llm_rank = 0` became ambiguous today.** After the gate reorder in section 17,
a candidate that is not actionable skips the advisory block but is still written
to the slate, so `llm_rank = 0` now means EITHER "ranked last" OR "never
ranked". MLM's 09:03 slate entry is the first instance. The slate exists to make
Gemma's ranking falsifiable, which was worth superseding window 3 for, so this
wants a distinct sentinel before more slates accumulate and any scoring treats
skipped candidates as badly-ranked ones.

**Not a finding, for the record:** there is no PDF stock list and there never
was. The seven `tmp/pdfs/chart-patterns-*.png` files are scanned pages from "How
to Trade Chart Patterns", added in the baseline commit `1843c82` and wired into
nothing. The universe comes from Wikipedia index-membership pages via
`WIKI_API`, with point-in-time membership so backtests do not inherit
survivorship bias.

---

## 23. 15-minute bars: fewer setups AND worse. 30m is bracketed.

Section 18 found a monotonic trend -- 30m beat 60/90/120/240, significantly from
90m out -- and 30m was the FINEST interval in the store. That was a data limit,
not a finding, so 501 symbols of native 15m SIP bars were collected (6,693,701
bars, 13,440 per symbol, 26 per session, validated).

The extrapolation was that finer would be better and would roughly DOUBLE the
setups. Both halves of that were wrong.

```
  bar   setups   hold       ALL     TRAIN   HOLDOUT     win
  15m      412   120b    -0.045    -0.076    +0.016   31.6%
  30m      741    60b    +0.012    -0.125    +0.288   35.9%

  15m vs 30m on holdout: -0.271R  n=138/247  p=0.161
  setup rate: 15m gives 0.56x the opportunities
```

**Fewer setups: 0.56x, not 2x.** The likely cause is `min_risk_pct = 1.5`. On
15m bars swing lows sit closer together, so entry-to-stop distance is smaller
and far more setups fail the floor. The pattern must also complete within the
same number of BARS, which is half the wall-clock time. A finer bar does not
create more structure; it fragments the same structure into pieces that no
longer clear the risk floor.

**And worse: -0.045R against +0.012R.** Not significant (p=0.161) but consistent
in direction across ALL and HOLDOUT.

So the trend REVERSES below 30m. 30m is now bracketed on both sides and looks
like a genuine optimum rather than merely the best available.

### What this closes

"More opportunity" is not reachable by widening in the two obvious directions:

  universe    all 1504 symbols adds 20 tradeable names (section 20)
  timeframe   finer bars produce FEWER setups and worse ones (here)

Setup scarcity -- which Gemma named as the top priority when audited, and which
is the binding constraint on every "we cannot tell yet" in this project -- has
one remaining source: THE SHORT SIDE. Bearish sweeps are invisible to the
screen rather than rejected by it, and adding them roughly doubles the sample
while being the only thing that makes higher-timeframe bias a SELECTOR rather
than another filter.

---

## 24. The filters ARE the wrong shape. Exploiting it still fails.

The operator's point, and it was right as a description: `min_risk_pct` and
`min_reward_risk` are NOT independent. Both come from the same three prices --
a tighter stop lowers risk_pct and RAISES reward_risk at the same time. So two
separate floors measure one thing twice from opposite directions, and cut a
RECTANGLE out of a space that is not box-shaped.

The surface, 12259 raw setups, no filters:

```
stop %        rr 0-1.0  rr 1.0-1.5  rr 1.5-2.5  rr 2.5-4.0   rr 4+
0-0.75           -0.01       -0.02       -0.00       -0.07    -0.18
0.75-1.5         -0.01       -0.04       +0.01       +0.01    -0.12
1.5-2.5          +0.02       -0.03       +0.03       +0.13    +0.41
2.5-4.0          +0.01       -0.07       +0.07       +0.33    +0.21
4.0+             +0.05       -0.01       -0.50         .        .

live rectangle (stop>=1.5 AND rr>=1.5): n=900, +0.100R
```

The good region is the diagonal bottom-right: wide stop AND high reward:risk
together. The rectangle also scoops up the +0.03 and -0.50 cells and excludes a
mildly positive region at wide stops with low R:R. Worst cell on the grid is
tight stop with high R:R (-0.18) -- a lottery ticket, a half-percent stop with a
target four times away.

### The fix that follows, and does not survive

Multiply them and the diagonal becomes one number:
`target distance % = risk_pct x reward_risk = (target-entry)/entry`.

```
            TRAIN              HOLDOUT
<2%       +0.002R n=5531     -0.072R n=2453
2-4%      -0.093R n=1648     -0.082R n=946
4-6%      +0.189R n=553      -0.057R n=381
6-10%     -0.224R n=312      +0.271R n=216
10%+      -0.258R n=132      +0.323R n=87

train picked 4-6% over 10%+, gap +0.447R
holdout gap -0.380R, p=0.125, SIGN FLIPPED
```

Train's best band is holdout's second-worst; train's worst is holdout's best.

### What this actually establishes

This is the twenty-third null and the most informative one, because the
DESCRIPTION was right and the exploitation still failed. The diagonal exists in
the full sample and inverts when the sample is cut.

It confirms what the model said unprompted before any of it was run: "a
spurious correlation exploited by the system that breaks down when conditions
shift". The train/holdout inversion was treated here as a volatility confound to
explain away. It is not a confound. It is the finding, and it reproduces at
every level examined -- filters, timeframes, exits, hold durations, VIX bands,
relative strength, and now risk geometry.

Structure that vanishes under every split is structure that is not there.

---

## 32. The core: S&P trend rule (Faber 2007) -- insurance, not edge. And the bar everything must clear.

`core_trend_test.py` (0e63ede), SPY with dividends, T-bills when out, 10bps
per switch, rules fixed before running, judged on post-publication years.

```
                     2007-2026 out of sample          1997-2006 (Faber's sample)
trend rule      +7.95%/yr  DD 26.5%  vol 12.8%      +8.94%/yr  DD 19.0%
buy and hold   +11.00%/yr  DD 55.2%  vol 19.5%      +6.17%/yr  DD 47.5%
verdict: drawdown halved, return -3.05pp/yr -> FAIL (needed within 2pp)
```

2008: +0.4% vs -36.8%. The cost is whipsaw -- out and back in at a higher
price in 2010, 2011, 2015, 2019, 2023. Post-publication it bought crash
insurance at ~3%/yr; before publication it also won on return.

THE BAR: buy-and-hold S&P paid +11%/yr since 2007 for no work. Any strategy
here has to beat that after costs, taxes and its own drawdowns to justify
real money. Nothing tested in this repo yet does on evidence it was not
chosen from.

## 31. Momentum out of sample (S&P 400 + 600) -- STRONG PASS on the pre-set rule

`momentum_oos_test.py`, rules and pass rule committed before running
(799c331). Same as the S&P 500 run: top 10 by 12-1, 63d hold, 21d step, 10y,
point-in-time membership, vs the index and a 5-seed random control.

```
               25bps excess/hold  NW t   random   mom-random   pre-2021   2021-on
MidCap 400        +3.90%          2.65   +0.94%    +2.96pp      +5.51%     +2.98%
SmallCap 600      +1.85%          1.28   +1.02%    +0.83pp      -2.35%     +4.26%
pooled NW t 2.75 (series concatenated -- loose); 50bps: 2.48 / 1.11
```

The first idea to pass a test on data it was not found on. Caveats, in
order of weight: (1) delisted members never priced -- 146 of 802 (400) and
242 of 1090 (600); random picks beat the index by ~1%/hold for that reason,
so MOMENTUM MINUS RANDOM is the honest number. (2) Small caps lost to
random 2017-2020. (3) The pooled t joins two series end to end.
Next: 2000-2015 large caps (covers 2008-09, the momentum crash), then a
paper dry run of the monthly job in November, outside window 15.

### 2000-2015 S&P 500 (`momentum_early_test.py`, 0983f79/75d36eb) -- PASS on paper, NULL in substance

```
187 books 2000-06..2015-12; 223 of 667 members never priced (heavy survivorship)
momentum excess over S&P  +2.52%/hold  NW t 2.48   (survivorship lifts this)
momentum minus random     +0.24pp/hold NW t 0.22   <- the honest number: zero
2000-02 -3.32pp   2003-07 +4.81pp   2008-09 -6.93pp   2010-15 +0.07pp
worst book 2008-07-09 -49.4% (S&P -20.0%); three-tranche max DD 62.4%
(21-day sampling, survivors only -- understated) vs S&P 47.2%
```

The momentum crash, as published. Top-10 unhedged momentum earns in calm bull
markets (2003-07, 2016-26) and gives it back in reversals. NOT fundable as
designed: the GO_LIVE 15% drawdown stop would have tripped in 2008 deep in a
loss. Remaining question: a published crash guard (market trend filter,
volatility scaling, broader book), committed first and judged on all three
datasets at once -- 2000-15 has now been seen, so a guard tuned on it alone
proves nothing.

### Crash guards (`momentum_guard_test.py`, 4285dab) -- every arm FAILS the pre-set rule; A misses by 0.01

Universe chosen by the operator 2026-10-05: S&P 500 + MidCap 400 (the 400's
membership log starts 2012, so 2000-15 is the 500 alone).

```
                CRASH 2000-15 (S&P 500)          LIVE 2016-26 (500 + 400)
arm        maxDD   mom-rand  NW t   worst     mom-rand  NW t   maxDD  worst  invested
base       62.4%   +0.24pp   0.22  -49.4%     +3.16pp   1.86   18.7%  -36.9%   100%
A trend    12.7%   +1.82pp   2.62  -27.4%     +2.38pp   1.49   22.2%  -36.9%  68/83%
B vol      43.2%   +1.00pp   1.18  -35.0%     +2.99pp   1.90   18.5%  -36.9%  84/88%
C broad    57.3%   -0.73pp  -0.97  -40.8%     +2.18pp   1.87   18.4%  -32.9%   100%
PASS needed: crash DD < 25%, crash edge >= 0, live edge > 0 with t >= 1.5
```

A (cash when the S&P closes below its 210-day average) cut the 2000-15
drawdown from 62% to 13% and turned that period's edge from zero to t 2.62,
but its live t is 1.49 against a 1.50 bar set beforehand. Recorded as a FAIL;
the bar is not moved after the fact. No guard caught the -36.9% book of
2016-26 (the fast 2020 crash). Strongest candidate found here; the only clean
evidence left is forward data -- a window-16 PAPER run with criteria set
before it starts, funding nothing until it earns it.

## 30. Gate 1 on the unseen 2018-2024 store -- the baseline fails, every candidate fails

`gate1_test.py`, `breadth_test.py`, `time_of_day_test.py`, `mpc_test.py`, all
rules committed before the 2018 store was read (08a4bee, 573d765, 49df1e6).
1,646 gated setups, 2018-01 to 2024-08, live gates, Friday flatten, 10bps.

```
baseline                    -0.041R  n=1646  account +35.9%  DD 21.9%
  2018-19 -0.061R   2020 -0.117R   2021 +0.067R   2022-23 -0.063R
1 R:R >= 2.0                -0.018R  gap +0.064R  p=0.381
2 sector leading            -0.027R  gap +0.030R  p=0.735   (2y lead p=0.030 gone)
3 daily ATR >= 4.8%         +0.035R  gap +0.113R  p=0.116   lost in 2020
7 insider buy, 90d          -0.104R  gap -0.076R  wrong direction
8 short ratio rising        -0.113R  gap -0.082R  wrong direction
5 MPC retest                -0.041R  n=6533  account -30.9%  DD 36.4%
breadth >= 6 of 11 sectors  -0.008R  gap +0.059R  p=0.400   2y: p=0.058
skip the 09:30 ET bar       -0.032R  gap +0.037R  p=0.648   2y: p=0.014
```

**Gate 1 fails on its first box (expectancy > 0).** The account's +35.9% is
SLOT LUCK, not edge: the 1,191 setups the 5-slot account took averaged
+0.078R, the 455 it skipped -0.354R. On the 2y store the same split is
-0.047R vs -0.087R. A selection effect that size in one store and not the
other is not something to fund.

Both new ideas from today (sector breadth, opening bar) passed or nearly
passed on the 2y store and vanished on the unseen one -- the pattern of this
whole list. Sector breadth is the second null for breadth (see §25).
MPC's "breakout entry on the same breakouts" (+0.15R both halves) is
LOOKAHEAD: it picks breakouts that later retested. Not tradeable.
Candidate 6 (momentum engine) has no unseen data here: its 10y run already
covered 2018-2024. Candidate 4 (news) needs headlines before 2024 (15 days on
disk); backfill + gemma labelling would be needed.

Exits, same day (`target_test.py` 850d994, `ladder_test.py` cd603e3, both
fixed before running): closer targets (0.5/0.75/1/1.5R, 0.5/1 x daily ATR)
are negative per trade on BOTH stores -- 0.5R wins 64% on 2018-24 and needs
67%. Ladders of 3-4 rungs, with or without the stop moved to breakeven, lose
more than the plain bracket on the 2y store (-0.081 to -0.102R vs -0.059R);
not run on 2018 for that reason. A fixed-stop ladder is arithmetically the
average of single-target arms. The exit only re-divides the loss: the
setups that go straight to the stop are -1R whatever the target.

Reading: the sweep setup is break-even before costs and slightly negative
after, across 8.7 years and every regime tried. Daily ATR >= 4.8% is the only
lead left, and it is weak.

## 29. resolve() holds through the weekend; live does not. -0.063R/trade

`backtest.setups.resolve` has no Friday flatten. Nearly every backtest here
resolves through it; live closes everything before the weekend
(`flat_before_weekend=True`). `flatten_gap_test.py`, 1468 setups, 2y:

```
resolve              +0.039R  total  +57.1R
walk, no flatten     +0.039R  total  +57.1R   identical on 1468/1468
walk, flatten        -0.024R  total  -35.6R   <- what live runs
flattened 549/1468 (37%): held +0.553R, flattened +0.384R
```

The simulators agree on everything else; the whole gap is the weekend.
resolve() does model the gap risk (a Monday open through the stop fills at
the open), and the sample includes the 2025 drawdown. On the 307 gated
setups since 2026-03-17: +0.395R resolved vs +0.216R flattened.

Consequences:
- Absolute R from any resolve-based test overstates live by ~0.06R/trade
  (more in the recent sample). Between-group comparisons share the bias and
  mostly stand; quoted levels (e.g. sector-leading +0.692R) are inflated.
- The flatten's measured cost has moved: -0.213R (HANDOFF), -0.012R (window
  11 note), -0.063R here. It was adopted as a RISK decision, not an edge
  one. With 23x5 trading from 2026-12-06 the weekend changes anyway.
- [ ] Window 16: decide flat_before_weekend on this, as a risk preference.
- [ ] Give resolve() an optional flatten and make new tests use it, so a
      backtest measures the strategy that runs. Until then, use the walk.

### Model's idea: 5-day strength vs the stock's own sector -- null, and backwards

From gemma4's strategy brainstorm, the one idea genuinely unlike the RS nulls
(short horizon, sector-relative). `short_sector_rs_test.py`, 1458 setups,
Friday flatten applied, bands +/-2pp fixed first:

```
                 TRAIN               HOLDOUT
beating sector   -0.225R n=100       +0.202R n=45
in line          -0.171R n=301       +0.012R n=142
lagging sector   -0.092R n=571       +0.269R n=299
train picked lagging over beating; holdout +0.067R, p=0.800
```

The model's mechanism predicted the top row wins; the bottom row did, in both
halves, though not significantly. 59% of setups lag their sector into the
trigger -- a sweep IS recent weakness -- so a strength filter mostly filters
out the pattern. The same lesson as the stock-RS null.

### Position-size cap 20% -> 30%: worse. And the account loses over two years.

`position_size_test.py` runs the account as it trades: 908 gated setups
2024-09..2026-09, time order, max 5 open, compounding, cash-limited, 10bps
round trip, Friday flatten. The 20% cap -- not risk_pct -- sets real risk:
at 1% risk it only stops binding beyond a 5% stop, so actual risk is ~0.44%.

```
config               return   max DD   risk/trade
cap 20% (live)       -11.1%   23.6%    0.44%
cap 25%              -14.5%   28.5%    0.50%
cap 30%              -13.4%   30.0%    0.55%
cap 30%, 2x margin   -17.3%   32.6%    0.63%
both halves negative at every cap (20%: -9.7%, -1.6%)
```

Size amplifies an edge; with costs and the weekend flatten there is none to
amplify. The cap stays. Note also 258 setups skipped for a full book: slots
bind in the backtest, so trade count is not only setup rarity.

### Model's idea: opening-gap reversion -- null as a strategy

`gap_reversion_test.py`, long-only, rules fixed first: gap down >= 2%, bar 2
sweeps bar 1's low and closes back above it, entry at bar 2's close, stop the
session low, target the prior close, out by the session close. 10bps in R.

```
                         TRAIN               HOLDOUT
sweep (the idea)         +0.062R n=542       -0.012R n=256   t +0.62 / -0.10
all gap-downs (control)  +0.051R n=2598      -0.129R n=1336
sweep vs no sweep        +0.014R             +0.146R  p=0.536
gaps >= 4%, sweep        -0.339R n=153       -0.210R n=71
gaps >= 4%, control      -0.193R n=858       -0.171R n=467
```

No edge on its own and the sweep adds nothing distinguishable from chance.
The one stable cell is a warning: gaps of 4% or more lose in all four rows --
news-driven gaps continue, the model's own named risk. Do not fade them.

### Volatility buffer under the stop -- null, the familiar flip

`stop_buffer_test.py`: stop = higher low - k x ATR(14, 30m) at the trigger;
entry and target unchanged; R against each arm's own risk; Friday flatten.

```
buffer      TRAIN    HOLDOUT  stopped  vs live (holdout)
0 (live)    -0.128   +0.185   48%
0.25 ATR    -0.118   +0.168   44%      -0.017R p=0.401  FLIP
0.5 ATR     -0.102   +0.132   41%      -0.053R p=0.031  FLIP
1.0 ATR     -0.078   +0.120   35%      -0.065R p=0.063  FLIP
```

Every buffer helps on train and hurts on holdout. It does cut stop-outs
(48% -> 35%), but a wider stop shrinks every winner in R. The paper's
invalidation-plus-buffer layer does not earn its place on this setup.

### Reward:risk: a forced 1:5 fails; the closest natural targets are the losers

`reward_risk_test.py`, 916 gated setups, 10bps in R, Friday flatten.

```
natural R:R (target = range high)   1st half    2nd half    2y total
1:1.5-2                             -0.267R     -0.166R     -77R   <- worst in both
1:2-3                               -0.172R     +0.369R     +20R
1:3-5                               -0.204R     +0.244R      +5R
1:5+                                -0.184R     +0.227R      +5R
target forced to 5R: hit 3.5% (needs 16.7%), -0.080R vs own target -0.051R
min_reward_risk 2.0 instead of 1.5:  1st half -99R -> -52R, 2nd half +51R -> +82R
```

Stretching every target to 5R fails in both halves -- the setups rarely run
that far. But the 1.5-2 band is the worst in both halves and alone loses more
than the strategy does; dropping it improves both. HANDOFF records 1.5 as
tested in August on 60 days of Yahoo data.
- [ ] Window 16 candidate: `min_reward_risk` 2.0. Needs a permutation test
      and the 2018-2022 data before it moves.

### Momentum Pullback Confirmation (breakout -> retest -> confirm) -- loses less, still loses

`mpc_test.py`, rules fixed first (daily trend, 40-bar resistance, close above
on 1.5x volume, retest within 0.5% holding resistance - 0.5 ATR, confirmation
close above the prior high, stop under the retest low, 2R target). 7,180
breakouts, 10bps, Friday flatten:

```
                                        TRAIN     HOLDOUT
buy every breakout candle               -0.126R   -0.173R
retest + confirmation (MPC)             -0.034R   -0.099R
breakouts that never retested           -0.323R   -0.330R
(hindsight) breakout entry, later-retested  +0.131R   +0.072R  -- not tradeable
```

Waiting for the retest filters out failed breakouts and loses far less than
buying them all -- but still loses. A breakout that holds its retest was a
good breakout; bought at the breakout it earned +0.13R, but which ones will
retest is unknowable at the breakout, and the later, wider-stopped MPC entry
gives the edge back.

### The model labels the news; the news does not predict (pre-registered null)

`news_event_test.py`: gemma4:12b labelled 2,421 point-in-time headlines (3
days before each of 916 gated setups) by event type, direction and
materiality in ~15 minutes -- the job it is good at.

```
                     TRAIN              HOLDOUT
material negative    -0.285R n=174      +0.337R n=72    hypothesis: worse
material positive    -0.034R n=147      +0.569R n=82
quiet                -0.090R n=289      +0.040R n=152
negative - quiet     -0.194R p=0.176    +0.297R p=0.166  SIGN FLIPPED
```

The stated hypothesis -- bad news before a sweep resolves worse, after
Lopez-Lira & Tang's underreaction to bad news -- flips. Positive news beat
quiet in BOTH halves, but that was not the hypothesis: a lead for the
2019-2024 news on the unseen store. Descriptive, all periods: analyst
downgrade -0.367R (n=117) is the worst common event.

### MPC against the live sweep: less bad, not an edge (`mpc_vs_sweep.py`)

Same calendar halves (split 2025-09-14), same account simulation and live
sizing (1% risk, 20% cap, 5 slots, cash, 10bps, Friday flatten):

```
per trade           1st half           2nd half
sweep (live)        -0.206R n=371      +0.134R n=545
MPC                 -0.058R n=1453     -0.051R n=1560
account             2 years   max DD   1st half   2nd half
sweep (live)        -11.6%    23.9%    -7.0%      -5.2%
MPC                  +5.3%    17.3%    +2.3%      +2.7%
```

The account's +5.3% is slot selection, not edge: all 3,013 MPC trades average
-0.054R, the 1,139 the five slots happened to take +0.018R; stop width shows
no pattern. MPC is steadier and closer to breakeven than the sweep in both
halves -- a candidate for the unseen 2018-2024 test, not a replacement yet.

### The entry limit: +0.30% above the signal, checked against Yahoo's lateness

`limit_minute_test.py`, a year of gated setups (538) on 1-minute SIP bars.
Live orders land ~3.5 min after the trigger bar closes; 2 of 14 since the
August fixes landed a scan late (~33.5 min), when Yahoo delivered that
stock's bar late (market-wide the newest bar was on time in 71 of 72 scans).

```
entry           on time (3.5 min)    a scan late (33.5, after the guard)   weighted 86/14
at the signal   91%  +34.5R          75%  +26.3R                           +33.4R
+0.30%          99%  +46.7R          90%  +50.4R                           +47.2R  <- live
market          100% +43.7R          100% +55.6R                           +45.4R
```

The guard removed 99 late setups (18%) already 0.5% below entry. Applied
2026-10-05 as entry_limit_offset_pct (fingerprint 69da227b506e).

---

## 28. Window 16 candidate pipeline: TradingAgents as the advisory layer

Decided 2026-10-04: run TradingAgents BESIDE the pipeline through window 15
(record-only, `tradingagents_pass.py`), and decide this at the window's end
from what it measured. Nothing below is built.

```
inside the scan (fast, mechanical)          between scans (TradingAgents)
1 scan -> 2 filter -> 3 watch               pre-market + :06/:36 passes
4 confirm: NEWS GATE (only model call)      rates held + new slate names,
  + look up latest TA rating (no wait) <--- checkpointed, paused per scan
5 enter: rating stored on the trade
Gemma ranking: removed. Gemma review: replaced by TA's while-open rating.
```

Why: the ranking (~5 calls/day) and review (14-32/day) are record-only and
unscored-or-null, and they share the GPU with the one enforced model call.

TradingAgents cannot run INSIDE the scan: ~2 min/ticker against an entry
priced at the trigger bar's close, on the GPU the gate needs at that moment.

THE OPEN PROBLEM -- COVERAGE. Most entries are setups first seen by the scan
that trades them, so a rate-ahead design has no rating for them at entry.
Rating all ~500 names is ~16h. Candidate fix: a pre-market "near-setup"
watchlist (leading sectors, swing low forming), ~20-30 names, ~1h.

Data feed for window 16: live scans read Yahoo 30m bars, every backtest
reads Alpaca SIP. Yahoo is also late -- PSX's 13:30 ET bar missing at the
14:03 ET scan (2026-09-24, the late-signal fill). Move live bars to Alpaca
real-time SIP (Algo Trader Plus, $99/mo); the free tier cannot do it (SIP
delayed 15 min, real-time is IEX only). Trading-path change: window 16.
- [ ] `features/intraday.fetch_intraday` -> Alpaca SIP bars, same
      session alignment and drop_incomplete as the store

Measure during window 15, from what already runs:
- [ ] coverage: share of entries with a TA rating in the 3 days before
      (score_gemma.py, "before entry" group sizes vs trades)
- [ ] value: realized R by rating before entry and while open
- [ ] review replacement: TA while-open rating vs the Gemma review's calls
- [ ] mechanics after week 1: passes completed, pauses resumed, failures,
      seconds per ticker (logs/scheduled.log TRADINGAGENTS sections)

Enforcement ("skip entries rated Sell") only if value is measured, and only
at a window boundary.

---

## 27. Three nulls in one night: model take/skip, trend exits, indicator layers

All on the 2024-09..2026-09 Alpaca store, policy `ba95546dbdbb`, 2026-10-04.

### Model take/skip (`gemma_graded.py`), outcome hidden

gemma_desk.py documented a `--graded` mode that was never built, and its live
path read today's daily closes and today's headlines -- not point-in-time.
Built properly: 30m bars to the trigger, daily closes before the trigger's
date, headlines from `data/news_history` created before it. Only setups that
pass every live gate are asked (190 of 497 did not), since live never trades
the rest.

```
307 setups since 2026-03-17, gemma4:12b, think off
take all      +0.395R n=307  total +121.1R
model take    +0.481R n=161  total  +77.4R
model skip    +0.300R n=146  total  +43.7R
take - skip   +0.181R  p=0.420
```

Right direction, not distinguishable from chance -- and the skips were
PROFITABLE, so following the model would have given up 43.7R. Conviction
carries nothing: "strong" on 159 of 161 takes. Same night, `gemma_audit.py`
scored gemma4 2/2 and `gemma_consult.py` said "probably no edge, stop testing
judgement overlays". The model agrees with this result.

### Trend exits (`mfe_trail_test.py`), never before recorded

```
arm             ALL     TRAIN   HOLDOUT  win
baseline       -0.024  -0.134  +0.195   39.9%
trail @1R      -0.015  -0.084  +0.122   48.1%
trail @1.5R    -0.019  -0.107  +0.156   43.4%
structure @1R  -0.033  -0.107  +0.117   43.4%
```

Every arm beats baseline on train and loses to it on holdout. Win rate up,
expectancy down -- the same signature as `structure_exit_test.py`. The
two-stage idea (leave the stop alone until +1R, then trail higher lows) does
not survive. Note the baseline across both years: -0.024R.

### Indicator layers (`indicator_layers_test.py`)

A layered read brought in 2026-10-04 (200/50/21/9 EMA, RSI, MACD, volume, and
an evidence score of layers agreeing). 1468 setups, bar p<0.006 for 8 tests.

```
above 200 EMA   SIGN FLIPPED  holdout -0.099R p=0.590
swing stack     SIGN FLIPPED  holdout -0.236R p=0.378
RSI 50-70       SIGN FLIPPED  holdout -0.125R p=0.723
volume >=1.5x   high volume WORSE both halves, holdout -0.204R p=0.282
MACD positive   TRUE ON 965/979 -- carries no information
above 9 EMA     true on 950/979; RSI >=70 on 28/979 -- too lopsided to test
evidence 4-5    holdout +0.141R vs 2-3 +0.375R -- more agreement, WORSE
```

The MACD row explains itself: the setup is a sweep and a break, which makes
the histogram positive almost by construction. The indicator is not bad; it
is redundant with the pattern. Same for the 9 EMA and "healthy" RSI. And
stacking confirmations selects for entries after the move has happened.

### Model managing the exit bar by bar (`gemma_exit_graded.py`) -- it never acts

100 sampled gated trades since 2026-03-17, reviewed every 2 bars, bracket
kept, exit_early closes at the next open. Arm A is review_positions' live
prompt; arm B adds stop, target, open R, best R, bars left, every indicator
and swing structure since entry -- the live review is never told the stop.

```
baseline (bracket)   +0.183R  total +18.3R
A live prompt        +0.203R  2 early exits in 885 reviews, p=0.503
B full picture       +0.183R  0 early exits in 889 reviews
```

The live review's 118/118 "hold" is not missing information: given the full
picture it held 889 times out of 889. The prompt's "be reluctant" plus the
model's disposition make it a constant. It costs 14-32 calls a trading day
and cannot inform an exit -- first to go at window 16 (needs
scheduled_scan.bat, frozen), or be replaced by TradingAgents' while-open
rating if that earns it.

### Per-position exit plan (`gemma_exit_plan_test.py`) -- the model is WORSE, p=0.021

The model picks one of five mechanical plans at entry, point-in-time.
307 gated setups since 2026-03-17:

```
one plan for every trade      standard +0.216R  short_clock +0.134  quick +0.121
                              runner +0.091  breakeven +0.085
model's per-trade pick        +0.086R   (short_clock 153, runner 141, quick 13,
                                         standard 0)
model vs standard             -0.130R/trade, -39.9R total, paired p=0.021
hindsight ceiling             +0.586R   (+180R) -- unknowable at entry
```

Today's plan beats every alternative applied uniformly, and the model never
chose it. The first significant result of the night is against the model.
The ceiling says exit choice is worth a lot in principle; nothing here can
see it at entry.

Open: this test's standard (+0.216R) and gemma_graded's take-all (+0.395R) on
the same setups differ. gemma_exit_plan_test's walk applies the Friday
flatten; `backtest.setups.resolve` may not. Reconcile before either number
is quoted as THE baseline.

---

## 26. Sector leadership -- held direction, p=0.030. A lead, not a rule.

From the 2026-10-04 morning scan against 2026-07-14's: one sector of eleven up
on the month against six, 29% of names trending up against 49%. Live trades
split the same way, IT +1.45R (3/5) and everything else -0.19R (2/10), n=15.

`sector_leadership_test.py` bands each setup by its SECTOR's point-in-time
median 12-1 relative strength -- what `features/sector_strength.py` computes
live -- rather than the stock's own (that was §RS, null at p=0.999). Bands
fixed first: leading >= +5pp, lagging <= -5pp.

```
              TRAIN (2024-09..2026-03)   HOLDOUT (2026-03..2026-09)
leading       -0.045R n=146              +0.692R n=108  win 48%
neutral       -0.118R n=364              +0.277R n=133
lagging       -0.118R n=469              +0.203R n=248

train picked leading over lagging, gap +0.073R; holdout gap +0.489R, p=0.030
```

Against it: the train gap is small and the effect lives in the holdout, where
every band is positive -- breadth's shape. Roughly the 25th idea tested, so one
p=0.03 is about what chance supplies. Leading sectors were 17% of setups; as a
gate it would cut ~83% of an already scarce trade count.

For it: it is the first sector-shaped test not to flip sign, and it fits the
mechanism argument (a sweep needs someone defending the level; money is
flowing INTO a leading sector).

Next: the 2018/2020/2022 data, the same bar as the regime filter. Nothing
reaches the trading path during window 15.

---

## 25. Breadth as regime — null (24th). And what the three PDFs actually say.

**Breadth**, borrowed from `OMEGA_907/trading_playbook.py`, which aggregates
trend across ALL assets rather than reading one index. Genuinely different from
what had been tested here (S&P vs its own averages, p=0.201; VIX bands,
confounded) and it is what the morning scan's own headline pointed at: "nearly
half the stocks in the S&P 500 are at cross purposes with the rest of the
market". Median breadth in sample: 57% of names trending up.

```
              TRAIN              HOLDOUT
weak <40%   -0.197R n=292      +0.411R n=126
40-55%      -0.170R n=263      +0.385R n=121
55-70%      -0.038R n=306      +0.371R n=214
strong 70%+ -0.117R n=100      -0.453R n=19

train picked 55-70% over weak; holdout gap -0.041R, p=0.854, SIGN FLIPPED
```

Every band negative on train, every band positive on holdout, ordering
scrambled between them. The same shape as everything else.

### "Designing Structure-Aware Exits and Maker-First Execution" — the useful one

It diagnoses a mistake made here. `structure_exit_test.py` and three others all
raised win rate and cut expectancy, and the reason is that they fired FROM THE
FIRST BAR, cutting into the 25% of trades that reach target at +2.383R. The
paper's item 12 is the fix: do not tighten into a trail until the trade has
demonstrated it can run, reaching an MFE threshold such as 1R, "keeping
early-stage risk control separate from late-stage profit protection".

That separation does not exist here. One stop is both the invalidation level and
the only profit protection. The paper's layered contract:

  invalidation       structural break of the last higher low, PLUS a volatility
                     buffer so noise is not mistaken for a break
  profit protection  activates only after an MFE threshold, then Chandelier or
                     structure-step
  regime awareness   multi-timeframe, so the noisiest timeframe does not govern
  cost realism       "too tight" increases churn enough to overwhelm the edge

This repo has the first layer without the buffer, and none of the others.

It also independently describes the LLM architecture built here on 2026-09-28:
a deterministic execution engine with the model OUTSIDE the decision loop as a
reviewer, schema-constrained rather than free-form. Its own example of a useful
reviewer output is "MFE suggests trailing too early".

### "Universe Construction, Turnover, and Segmentation" — right playbook, wrong engine

About momentum ROTATION, so it applies to `momentum_factor.py` rather than the
sweep screen. Two transferable pieces: HYSTERESIS (rotate only when a new
candidate beats the held one by a margin covering costs), and a formal statement
of what was measured here all day -- "the lower L, the more likely it is that
the constituents will change hands from one holding period to the next", i.e.
selectivity raises turnover without raising quality. It also confirms the
liquidity floor from the other direction: momentum performs better among larger
names.

### "Algorithmic_Trading_Bot.pdf" — not useful

A 2021 conference paper surveying what algorithmic trading is, plus Indian
broker platforms. No methodology to act on and no result to replicate.

---

## 22. HTF liquidity pools — null, and the premise was wrong

The idea, and it was the best one of 2026-09-28: a sweep only matters if it runs
REAL resting orders, and orders cluster at levels people can see. So the 4h
should not veto a 15m setup — it should SUPPLY the level the sweep must take
out. That is a change to what `took_out` counts as a sweep, not a seventh filter.

Argued at the time to have the best prior of anything tested, being the 20M
floor's own mechanism argument (a sweep needs resting orders) applied to the
LEVEL instead of the INSTRUMENT.

```
entry 30m, pool from 240m, 741 setups
mix: htf pool 540 (73%), entry only 201 (27%)

               TRAIN                 HOLDOUT
htf pool      -0.135R n=356         +0.290R n=184  win 40%
entry only    -0.099R n=138         +0.280R n=63   win 38%

train picked "entry only"; holdout gap -0.011R, p=0.968, SIGN FLIPPED
```

Holdout bands one hundredth of an R apart. No effect.

**The mix refutes the premise outright.** 73% of sweeps ALREADY take out a
confirmed 4h swing low. The belief that most sweeps hit meaningless local lows
was wrong, so the refinement had almost nothing to separate. `took_out` reading
only the entry timeframe was not the weakness it looked like.

### The number that matters more than this result

Twenty ideas, twenty nulls. Declared trials are now roughly seventy — the
original 50 plus five timeframes, two exit tests, gap risk, accumulation twice,
relative strength, and this.

A Bonferroni threshold at seventy trials is about **0.0007**. The 20M liquidity
floor sits at **p=0.022**. It does not clear it.

That does not make the floor wrong — it has a mechanism behind it and it held
its direction across both halves. But this repo describes it as the one
validated result, and at the current trial count that description is no longer
defensible. The deflated Sharpe of 0.82 was computed against 50 trials and
should be recomputed against seventy before any figure here is quoted again.

### What follows

Stop testing filters. The search space has been sampled twenty times with one
near-survivor, and every additional test now costs more in correction than it
can plausibly return. The remaining leads are STRUCTURAL rather than selective:

1. the short side — bearish sweeps are invisible to the screen, not rejected by
   it, and adding them roughly doubles the sample
2. 2018/2020/2022 data — the regime filter is the only live lead and needs a
   third sample
3. live paper trades — the only evidence that carries no trial penalty

---

## 9. Overnight holds after 2026-12-06 — measure before deciding

23x5 trading begins 2026-12-06 (SEC approved Nasdaq's proposal April 2026;
NYSE Arca and 24X preliminary). Alpaca's own 24/5 overnight session already
runs 20:00–04:00 ET on Blue Ocean. `FREEZE.md` has the detail.

The exposure: **Alpaca supports limit orders only overnight**, so a bracket's
stop cannot fire between 20:00 and 04:00, and ~46% of these setups are held
overnight by design. No config changes this — the broker does not offer
overnight stops.

- [ ] Re-measure the overnight gap distribution **on data that includes
      overnight prints**. Yahoo regular-hours bars cannot see them, so the
      current numbers describe a market that stops existing on 2026-12-06.
      Alpaca's `boats`/`overnight` feed is the source.
- [ ] Quantify what a stop that cannot act until 09:30 costs, against the
      backtest's assumption of a stop that was always live.
- [ ] **Only then** decide between: accept the exposure, size down overnight
      holds, or stop holding overnight at all. Each is a policy change and
      cannot land inside the current window.
- [ ] Do **not** set `extended_hours = True` as a response. It adds no
      protection and lets entries fill overnight unprotected in thin
      liquidity. It is strictly worse than the present state.

### 9a. Overnight exit — built, on a branch, not deployed

`research/overnight-exit` (worktree at `../stock-scanner-overnight`) adds
`execution/extended_exit.py` and `scripts/close_extended.py`: a way to CLOSE a
position between 20:00 and 04:00, which currently cannot be done at all, by
hand or otherwise. Limit orders only, because that is all the broker accepts
out of hours.

- [x] Exit-only by construction — sides come from the broker's position signs,
      a flat book sends nothing, a second run cannot open a short.
- [x] Polls and **fails loudly when the limit does not fill**, rather than
      reporting a submitted order as a completed exit.
- [x] 20 tests. Full suite 782 green on the branch.
- [ ] **Never run against a live order yet.** Proving it needs a real position
      to close; opening one for the test would put a trade in the window the
      strategy never chose. First real use is the first proof — use `--symbol`
      on one name and watch it.
- [ ] Decide whether it is worth deploying at all. It **ends the window** when
      merged, and it is a manual tool, so the honest sequence is §9's
      measurement first — find out whether overnight exposure costs anything
      before spending a window on the fix.

This gates any read of live results after 2026-12-06, because the +0.218R
holdout was measured before the regime change and does not describe the same
market.

## 10. Calendar-aware wind-down — the flatten misses holiday weeks

`wind_down_from_weekday` blocks Friday entries; both flatten triggers are
Friday-only. When Friday is a holiday, Thursday is the last session before a
multi-day close and neither rule notices.

Four dates in the next twelve months: **2026-12-24**, **2026-12-31**,
**2027-03-25**, **2027-06-17**, every one a four-day gap. It recurs whenever a
holiday lands on a Friday, so this is permanent until fixed rather than a
December quirk. `MARKET_CALENDAR.md` has the table, generated from the broker
by `scripts/market_calendar.py`.

- [ ] Replace the weekday test in `should_wind_down` with "is this the last
      session before a gap of N+ days", read from the broker calendar. The
      docstring's "Thursday is Thursday whatever the month" is exactly the
      assumption that fails.
- [ ] Give the flatten a trigger on every session that precedes a multi-day
      close, not Friday alone.
- [ ] Account for early closes in the bars-available arithmetic. The Thursday
      decision assumes 17 bars; a 13:00 close leaves about seven.
- [x] Dates pinned in `tests/test_market_calendar.py`, with Thanksgiving as
      the control case that the Friday-only flatten does cover.
- [x] `sources/market_calendar.py` answers "is this the last session before a
      multi-day close" from the broker payload. Pure, tested, and imported by
      nothing on the frozen path, so it changes no live behaviour.

**Touches `policy.py` and `flatten.py`, so it ends the window.** Until then the
mitigation is manual: run `scripts/flatten.py --submit` by hand on 2026-12-24
and 2026-12-31. Idempotent, safe to re-run.

---

## Explicitly not doing

- **Merging to `master`.** Not until §1–§5 are done.
- **Tuning toward a better backtest.** Ten ideas were tested this session; one
  survived. The failures are recorded in `HANDOFF.md` §5 so they are not
  re-derived.
- **Funding the account.** Three scanner trades is not evidence.
