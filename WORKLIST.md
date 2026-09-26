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

- [ ] **HAC / Newey–West standard errors.** A 63-day hold with 21-day steps
      makes consecutive rebalances overlap by two-thirds. The engine's
      "clustered" t of **+2.42** treats rebalances as independent and overstates
      confidence. Use a lag of at least `horizon/step − 1` (= 2), or block
      bootstrap.
- [ ] Re-test after costs. Published large-cap momentum implementation runs
      **2–4% annually** against ~7% gross excess — potentially half the result.
- [ ] Only after both: decide whether +1.73% excess survives.

## 6. Momentum as a separate engine — not grafted onto trade.py

- [ ] Keep the two strategies apart. They differ in every mechanic:

      | SMC swing | momentum factor |
      |---|---|
      | event-driven entry | scheduled rebalance |
      | stop/target bracket | portfolio-level exit |
      | sizing from stop distance | sizing from portfolio weight |
      | needs a fresh setup | needs relative rank |

- [ ] Monthly rebalance loop, equal weight, no stops. `sizing.py` cannot size
      it — with no stop there is no input.

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
