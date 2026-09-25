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
- [ ] **Eight shadow dimensions are unredeemable without this**: RSI gate,
      session slot, weekday, LLM ranking, `swept_equal_lows`, and the
      uncapped-exit candidate.

## 3. Parquet/DuckDB storage

- [ ] Replace the JSON bar cache. Measured: full backtest compute ≈ **9s**,
      reading 500 cached symbols ≈ **104s**. I/O is 12× compute.
- [ ] This is the reason research iterations are slow, not CPU. A GPU would not
      help.

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

## 8. `fresh_bars = 2` — ANSWERED 2026-09-25. Leave it alone.

**Measured, `scripts/stale_setup_test.py`, S&P 500, 119 setups.** Each setup
re-entered N bars after its trigger at that bar's close, keeping the stop and
target it was built with, so the cost of delay falls out of the arithmetic
rather than needing a slippage model.

HOLDOUT expectancy decays monotonically, and crosses zero exactly where the
live gate sits:

        delay      holdout      takeable
        0 bars *   +0.141R       90%
        1 bar  *   +0.072R       90%
        2 bars *   +0.029R       88%
        3 bars     -0.052R       80%
        5 bars     -0.081R       82%
        8 bars     -0.409R       82%
        13 bars    -0.555R       65%
        20 bars    -0.960R       55%
        * admitted by fresh_bars = 2

Paired randomisation on the same setups fresh versus stale — paired because
every column holds the SAME setups at different entry points, so a two-group
test would overstate its confidence the way the clustered t in §5 does:

        HOLDOUT  0 vs  8 bars  n=33  +0.560R  p=0.005
        HOLDOUT  0 vs 13 bars  n=26  +0.579R  p=0.010
        HOLDOUT  0 vs 20 bars  n=22  +0.773R  p=0.027

**So the gate is correctly placed and widening it would buy trades with
negative expectancy.** Trade scarcity is the honest output of this strategy,
exactly as this section anticipated. 8,455 of 8,479 setups rejected as stale
is arithmetic, not a defect.

- [x] Outcomes by age bucket
- [x] Entry slippage — implicit: a later entry widens risk against the same
      stop, so the same move earns less R
- [x] Count surviving each successive gate — the `takeable` column, and note
      it stays above 80% out to 8 bars. Setups do not EXPIRE quickly; they
      stop being profitable long before they stop being enterable, which is
      why a survival-based gate would have been the wrong instrument.
- [ ] Setup age histogram — not needed for the decision and not done.

### The uncomfortable part

Holdout expectancy at delay 0 is **+0.141R**, and at the gate's own edge —
delay 2, which the scanner trades — it is **+0.029R**. The benchmark this
project quotes is a validated **+0.218R**.

Different sample (60 days of 30m bars here, against the original 86-day
study) so these are not the same measurement and should not be subtracted
from one another. But the direction is unwelcome and it is not noise: the
edge measured on data nothing was chosen from is thinner than the number
being planned against.

**This does not change the policy.** It is recorded because the next person
to quote +0.218R should see it.

My first theory here — that detection lagged the trigger — was measured and
proved wrong: all 133 setups are visible at age 0. Do not re-derive it.

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
