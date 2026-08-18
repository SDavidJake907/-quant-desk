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

- [ ] Stop changing `TradingPolicy`. Fingerprint moved **three times today**
      (`3195873fc5b6` → `cfd459a03db3` → `dcdf791c3035` → `9fe7a6618823`), so
      the trades already collected cannot be pooled.
- [ ] Declare an evaluation window with a start date and a minimum trade count,
      not a calendar length. ~2 setups/day across the index and a taper to 40%
      average deployment makes trades scarce; a week may produce single digits.
- [ ] Report results **grouped by fingerprint**. `positions.py` currently pools
      everything; it should split.
- [ ] Write the frozen fingerprint and window start into `HANDOFF.md` so a
      future session cannot silently reopen it.

**Note:** the partial-bar fix above changes behaviour but not the fingerprint —
it is a data-layer correctness fix, like the adjusted-price bug. Freeze *after*
it lands, which is now.

## 2. Audit records must carry outcomes

- [ ] Write realized R and exit reason back to `logs/trading_audit.jsonl`. It
      records what was decided and never what happened; realized R is recomputed
      on demand from Alpaca's order history, which is not permanent.
- [ ] Confirm every record carries the policy fingerprint (preflight and
      flatten records now do; check the rest).
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

## 8. `fresh_bars = 2` — investigate, do not loosen

Current funnel reads `setups 130, stale 130, candidates 0`. Collect before
touching it:

- [ ] Setup age histogram
- [ ] Outcomes by age bucket: 0–2, 3–5, 6–10, 11–20 bars
- [ ] Entry slippage from trigger close to the price actually available
- [ ] Expectancy after realistic spread and slippage
- [ ] Count surviving each successive gate

If older setups stay profitable **on untouched data**, widen it. If not, trade
scarcity is the honest output of the strategy and should be left alone.

My first theory here — that detection lagged the trigger — was measured and
proved wrong: all 133 setups are visible at age 0. Do not re-derive it.

---

## Explicitly not doing

- **Merging to `master`.** Not until §1–§5 are done.
- **Tuning toward a better backtest.** Ten ideas were tested this session; one
  survived. The failures are recorded in `HANDOFF.md` §5 so they are not
  re-derived.
- **Funding the account.** Three scanner trades is not evidence.
