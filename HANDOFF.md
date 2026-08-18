# Handoff — 2026-08-18

Branch `momentum-swing-research`, 12 commits ahead of `master`. 576 tests green.
Nothing merged to `master`. Paper account only; no live-trading flag exists.

**Policy fingerprint: `9fe7a6618823`** (was `3195873fc5b6` at session start).
It changed three times today — every change is listed under "Policy changes".

---

## 1. What runs, and when

All times Alaska. AK is ET−4 year-round; both zones observe US DST together.

| task | schedule | supervised | submits orders |
|---|---|---|---|
| `stock-scanner-scan` | 05:33–12:03, every 30 min | no (by design) | yes — entries |
| `stock-scanner-data` | 13:00 daily | yes, 3 restarts | no |
| `stock-scanner-flatten` | **Fridays 11:45** (15:45 ET) | no (by design) | yes — closes |

Scans fire at `:03`/`:33`, three minutes after each 30-minute bar closes.
`watchdog.py` refuses `--submit-paper` and `--live` outright: a process that may
have reached the broker and died before reading the response has unknown
position state, and restarting it could double a position. Only the read-only
data job is supervised.

Re-register everything with:

    powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1

Post-reboot check (Ollama does not auto-start):

    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1

---

## 2. Trading week

| day | deployed | cap/position | entries |
|---|---|---|---|
| Mon | 50% | 10.0% | yes |
| Tue | 40% | 8.0% | yes |
| Wed | 30% | 6.0% | yes |
| Thu | 0% | 0% | wind-down: no new positions |
| Fri | 0% | 0% | flatten at 11:45 AK |

Existing positions keep their brackets through Thursday and are closed Friday.

---

## 3. Policy changes made today

| field | value | why |
|---|---|---|
| `block_overnight_entry` | `True` | Entries across a session boundary are adverse **88% of the time**, mean +0.785%, worst +3.20% — over twice the 1.5% minimum stop consumed before the position exists. Same-session control is ~zero (−0.013R). A modelling correction, not an edge claim: the backtest measures entry at the trigger bar's close, and across a boundary that price was never available. |
| `flat_before_weekend` | `True` | **Costs −0.213R per trade** against a +0.218R validated edge. Adopted as a risk decision — an exposure that cannot be managed judged worse than the price of removing it. |
| `wind_down_from_weekday` | `3` (Thu) | Flattening Friday alone still lets a position open Friday morning with hours to go wrong. |
| `weekday_deployment_pct` | `(50, 40, 30, 0, 0)` | Risk taper. **Not a weekday edge** — see §5. Costs return roughly in proportion to the reduced exposure. |

**Unchanged, and deliberately so:** `max_positions=5`, `max_position_pct=20`,
`max_hold_bars=60`, `fresh_bars=2`, `min_risk_pct=1.5`, `min_reward_risk=1.5`.
Each was tested today and the current value won or the alternative was noise.

---

## 4. Bug fixed

**Daily bars mixed two price scales.** `closes` came from Yahoo's `adjclose`
(split/dividend adjusted), `highs`/`lows` from the raw `quote` block. On MO,
**95% of five years of daily bars had a close outside its own high/low range**
(KO 93%, AAPL 54%). Test fixtures set `adjclose == close`, the one case where
the bug is invisible, so nothing caught it.

Not cosmetic: `backtest.setups.resolve` tests stops and targets against
`lows`/`highs`. Levels from an adjusted close checked against raw highs sit
below almost every bar — a daily backtest would fill its target instantly and
report an edge that was pure unit mismatch.

Fixed with one adjustment ratio per bar applied to open/high/low, so each bar
keeps its proportions and ATR stays meaningful. `opens` added (daily bars never
had it, so `resolve`'s gap-fill path was unreachable on the daily timeframe).
**The intraday parser reads all four prices from `quote` and was never
affected — the live SMC path is unchanged.**

---

## 5. What was measured — the negative results

These matter more than the code. Each cost real time and each is now settled.

**Momentum swing strategy — dead.** Gave `screens/trend.py` entry/stop/target
and measured it. Loses to random entries with identical management in **38 of
40 configurations**, at every stop width from 1 to 4 ATR. Ladders don't fix it.
Four of six screen criteria have a positive edge *alone* while all six ANDed
are negative (−0.087R), which points at the AND delaying entry to maximum
extension. Best single criterion measured **+0.429R on train and +0.014R on
holdout**. No demonstrable selection value, in whole or in part.

**Equal-lows liquidity pool — unproven.** Sweeping a pool of near-equal lows
beat isolated lows by +0.778R train / +0.543R holdout at a 0.10% band — then
**inverted to −0.077R on train when the band widened to 0.30%**. 14 and 10
trades per bucket. Recorded as `Setup.swept_equal_lows`, never gated on.

**Weekday effect — noise.** Mon +0.539R, Tue +0.054R, Wed −0.562R, Thu +0.780R,
Fri +1.068R over 125 setups. A permutation test says a random five-way split
produces a best day at least that good **18.9%** of the time. Train and holdout
disagree about the winner.

**More slots — worse, not better.** Under a real 100% deployment cap: 5×20%
gives median 34.1%, 10×10% gives 19.9%, 20×5% gives 9.8%. The binding
constraint is **setup arrival** (~2/day, ~1.5 session hold, so concurrency is
3–4), not slots. Margin adds nothing: 10 slots at 20% deploys 200% and returns
34.3% against 34.1% unlevered. Return/drawdown is flat at 4.7–5.1 across every
configuration — these differ only in capital deployed, not edge quality.

**Shorter holds — no cheap trade.** Weekend exposure barely moves until the cap
reaches one session (19% vs 38%), and that costs half the expectancy. Both
routes to avoiding weekend risk cost ~50% of edge, because this is
structurally a multi-day swing strategy.

**Uncapped SMC exit — promising, unproven.** Removing the range-high target
gained +0.291R train (t +1.72) / +0.320R holdout (t +1.14). But only 30 of 84
trades change, direction is near a coin flip (17 better, 13 worse), and one
holdout trade contributes ~half the mean. **It also raises weekend exposure
from 38% to 54%.** Not adopted.

---

## 6. What worked

**Cross-sectional momentum factor** (`screens/momentum_factor.py`) — the only
thing all session that beat random *and* cleared significance.

S&P 500 point-in-time, 10y, 63-day hold, monthly rebalance, top 25:

| screen | mean excess | t (clustered) |
|---|---|---|
| **momentum** | **+1.73%** | **+2.42** |
| random | +0.11% | +0.28 |
| trend | −0.07% | −0.45 |
| coiled | −0.41% | −1.38 |

Robustness, all parameters taken from the 1993 literature before the first run:
portfolio size decays monotonically (top 10 +3.16%, top 25 +1.73%, top 50
+0.82%); annualised excess peaks at a 3–6 month hold (~7%) and falls at one
month (~4%), reproducing the original paper's shape; recent 5y sub-period holds
(+2.87%, t +1.99).

**Not tradeable yet.** No cost model (published large-cap momentum
implementation runs 2–4% annually against ~7% gross excess). 80 index members
have no price history and deletions skew toward failures, so every number reads
high. One regime, containing no momentum crash.

It is **not wired to `trade.py`** and needs a rebalance loop, not a bracket
path — with no stop, `sizing.py` has no input.

---

## 7. Observability added

`0 fresh setups` used to mean three different things. It now reads:

    funnel: 501 symbols  setups 130  stale 130  not live 0  overnight 0
            slot/weekday 0  candidates 0  oldest rejected 727 bars (window 2)

**This is the most important number to watch.** 130 setups found, all 130
refused as stale. The screen is not quiet — `fresh_bars = 2` admits almost
nothing. A week of this pattern makes that the next thing worth a holdout.

**News-gate health** is probed once per run, before any candidate exists (a
scan finding nothing never calls the gate, so per-candidate logging would miss
an outage on exactly the quiet days that look normal):

    news gate OK — gemma3:12b reachable
    NEWS GATE FAILING OPEN — Ollama unreachable, gemma3:12b absent; ...

A degraded probe writes a `news_gate_degraded` audit record. Before this, an
Ollama death was **invisible** — grep the whole log for "not checked" and you
get zero hits, and always would.

---

## 8. Gemma, end to end

Two roles, cleanly separated:

- **Ranking — advisory only.** `ranking.rank()` produces conviction/driver/
  concern and an ordering, printed as "advisory, not acted on", written to the
  audit as `llm_rank`/`llm_conviction`/`llm_driver`/`llm_concern`. It **never
  reorders the send queue** — orders go out in deterministic `priority()` order.
- **News gate — enforcing, veto-only.** Gemma labels a headline's category and
  materiality; the blocking decision is plain Python over those labels
  (`STRUCTURAL = {"legal"}`) plus SEC item codes. It can refuse, never approve
  or size. Fails open by design, now loudly.

The LLM emits no number anywhere in the trading path.

---

## 9. Account, as of 2026-08-18

Opened 2026-08-06 with $100,000 paper. Equity **$101,484.30**, flat, no open
positions or working orders. 35 fills, 8 round trips.

| | |
|---|---|
| net realized | **+$1,487.14** |
| winners (4) | +$3,064.41 — Q, EXPE, CEG, NVDA |
| losers (4) | −$1,577.27 — AMD, IP, CCL, SUPN |

**Only 3 were scanner trades** (EXPE +$1,134.60, AMD −$232.71, CCL −$411.76 =
**+$490.13**). The other 5 (+$997.01) were opened outside the system and are
excluded from `positions.py` R statistics.

Three scanner trades is not evidence. The +0.525R live expectancy has a
standard error several times the effect.

**EXPE, the biggest winner, was an overnight-gap entry** — ordered 18 hours
after its trigger bar, which `block_overnight_entry` now prevents. It won,
which is the worst way to learn anything.

---

## 10. Open, in the order I'd do them

1. **Watch the funnel for a week.** If `stale 130 / candidates 0` repeats,
   `fresh_bars = 2` is the real constraint on this system. Needs a holdout, not
   a guess — my first theory about this (detection lag) was measured and proved
   wrong.
2. **Cost model on the momentum factor.** Turns ~7% gross excess into a number
   you can decide on. Highest-value work on the one thing that beat random.
3. **Delisted price history** (Tiingo; `SCANNER_TIINGO_API_KEY` exists). Gates
   the momentum result — 80 index members have no history and deletions are
   disproportionately failures.
4. **Parquet/DuckDB storage.** Measured: full backtest compute is ~9s, reading
   500 cached symbols is ~104s. I/O is 12× compute. GPU would not help; this
   would.
5. **Write outcomes back to the audit log.** It records what was decided and
   never what happened. Eight shadow dimensions are unredeemable without it.
6. **Universe decision.** Collector runs `--index all` (1,504 symbols); scanner
   defaults to `sp500` (501). A thousand symbols collected daily and never
   scanned. Needs the cost model first — small caps make costs bite harder.

---

## 11. Commands

    # health after a reboot (Ollama has no service)
    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1

    # what the account actually holds — read-only, reads the BROKER
    .venv\Scripts\python.exe scripts\positions.py

    # scan without sending anything
    .venv\Scripts\python.exe scripts\trade.py --dry-run

    # close everything now — dry run first, then --submit
    .venv\Scripts\python.exe scripts\flatten.py
    .venv\Scripts\python.exe scripts\flatten.py --submit

    # stop all trading immediately
    New-Item C:\Users\kitti\stock-scanner\STOP_TRADING

    # research (all read-only, none touch the broker)
    scripts\momentum_backtest.py      pattern vs random, daily bars
    scripts\momentum_ladder.py        exit-rule grid with random controls
    scripts\momentum_portfolio.py     slot-constrained portfolio sim
    scripts\screen_ablation.py        per-criterion edge, leave-one-out
    scripts\equal_lows_test.py        liquidity-pool partition
    scripts\overnight_gap_test.py     cost of entering across a session
    scripts\weekend_flat_test.py      cost of going flat Friday
    scripts\hold_length_test.py       hold cap vs weekend exposure
    scripts\slot_count_test.py        slots x position cap, buying-power aware

---

## 12. Things that will bite you

- **`fresh_bars` counts bars, not time.** Overnight is one bar and 17 hours.
  `block_overnight_entry` covers the entry case; anything else reasoning in
  bars-as-time has the same hole.
- **The news gate fails open.** By design — a model outage must not stop the
  deterministic strategy. It is now loud, but it still does not block.
- **Paper fills are optimistic.** Mean stop fill came in **+1.50 better than
  the stop level**. Real fills gap through stops. The loss side of every result
  here is understated.
- **60 days of intraday history is one regime** and about 8 weekends, none of
  them bad. Everything measured on it is under-sampled in the tail.
- **Survivorship.** Point-in-time membership fixes the inclusion half; Yahoo
  serves no history for delisted names, so the failure half remains.
- **`git bash` mangles Windows paths.** `cmd.exe /c "scripts\foo.bat"` from bash
  silently does nothing and returns 0. Use the PowerShell tool.
