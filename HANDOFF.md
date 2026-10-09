# Handoff — 2026-10-06 (Tuesday ~11:10 AK) — for a REMOTE session

Branch `momentum-swing-research`, clean at the commit that adds this file.
**1100 tests green.** Paper account only, **$102,040.97, flat.**

## 0. Read first: what a remote session can and cannot do

The LIVE SYSTEM RUNS ON THE HOME PC ONLY (Windows, scheduled tasks, Ollama +
gemma4:12b on an RTX 5060 Ti, the TradingAgents venv, `.env` with Alpaca PAPER
keys). A remote session gets **the code, docs and tests only**. NOT in git:
`data/` (3.9 GB: price stores, factor panels, partial earnings, insiders,
short volume, news), `.env`, `logs/`, `tmp/`. So remotely: code, docs, tests
and research DESIGN are fine; anything needing data, the broker, Gemma or the
GPU waits for the home PC. **Do not touch the trading path** (FREEZE.md list)
without the operator — any change ends window 16.

## 1. What's live now — window 16 (declared today)

| | |
|---|---|
| policy fingerprint | `1c5ed20750e6` (window 34: 50/50, extended hours 24/5 for both books, quarterly stakes from 2027) |
| code build | `e67b955` |
| universe | **`watchlists/core.txt` ONLY**: AMD, MRVL, INTC. The operator hand-picks names weekly/daily. **Commit every edit** (each trade must trace to its list). Bitcoin-linked names stay out (stocks only). Empty file = back to the S&P 500 |
| why | the full-S&P sweep found nothing in the Oct 2-6 rally (4.6-18% of stocks swept a 10-day low vs 27-45% in late Sept); the operator prefers their own picks |
| expect | few trades: the sweep needs a dip below a recent low |
| window 15 | closed today, 0 completed trades |

Schedule (Alaska time) is unchanged — scans 05:33-12:03 every 30 min, flatten
before 3+ day closures, data/outcomes/news jobs after the close. Changed today
or yesterday, all committed:
- `protect.py` SELLS a position whose stop is already breached (+ Desktop
  `PROTECTION_ALERT.txt`) instead of leaving it naked.
- TradingAgents (record-only): structured output fixed (JSON schema);
  premarket is **connected to the scan**: rates core list -> held -> slate ->
  last-5-session live setups -> `watchlists/kraken.txt`, 30 names premarket,
  8 per live pass. Rule for it ever acting: TRIALS #44.
- `review_positions.py` = **monitor v2**: Gemma gets the trade math as codes
  (`monitor/codes.py`), a chart-pattern card with its measured record, 48h
  headlines; temperature 0 + seed; record-only. Rule for it ever acting: #45.
- All prompts share one event vocabulary (`monitor/events.py`); registry in
  `apprentice/PROMPTS.md`.

Built but NOT scheduled (run by hand): `momentum_rebalance.py` (dry run),
`momentum_watch.py` (daily after close), `momentum_report.py` (TradingAgents
on the picks), `risk_monitor.py` (3%/6%/15%, record-only), `notify_watch.py`
(ntfy; the operator declined phone alerts), `watch_list.py` (view-only).

## 2. Research since 10-05 — ~101 trials, 0 surviving passes (TRIALS.md)

Everything built from price/volume alone failed on data it was not found on:
Gate 1 (sweep −0.041R on 2018-24), every sweep filter/exit/ladder, MPC,
breadth (twice), opening bar, momentum add-ons, 12 indicators, the GPU factor
lab (10 signals, Romano-Wolf), 10 LMW chart patterns (bullish = bearish),
the operator's `signals.py` (entries WORSE than random, t −4.2/−4.1), and the
skills bundle's VCP / follow-through / distribution days (two backwards).
Momentum + trend filter is the best lead but its 2016-26 edge sits in five
names. Low-vol and MAX going the wrong way in both periods = survivorship in
the free data. Details: WORKLIST §30-§36.

GPU research: `research_gpu/` runs in the Microsoft Store Python 3.12 (torch
2.11 cu128 on Blackwell sm_120, CuPy, pandas), reading panels written by
`scripts/factor_export.py` / `index_export.py`. Refuses market hours.

## 3. Next, in order

1. **Earnings download**: 2,410 of ~4,200 days (to 2019-05). Resume AFTER
   the close, in its own terminal (Claude Code's memory guard killed it twice
   during market hours): `uv run python scripts/collect_earnings.py`. Then
   `scripts/pead_test.py` (rules already committed, #27).
2. **Momentum November dry run**: `momentum_rebalance.py` early November,
   `momentum_watch.py` after closes, `momentum_report.py` after the rebalance.
3. **Low-float day-trading historical test** (#70-71): scoped, NOT started,
   waiting for the operator's go. Alpaca free plan has 1-min SIP premarket,
   historical NBBO quotes, 19,169 delisted symbols and news; float via SEC
   shares outstanding. Expect it to fail (day-trader studies: >97% lose).
4. **Operator decisions pending**: Norgate survivorship-free data; Alpaca
   $99 SIP feed; FRED key; Ollama 1 parallel slot (memory); core-list names.
5. **Window boundary**: `TradingPolicy.as_prose()` is stale (calendar
   flatten, +0.30% limit, breached-stop sale); drop the null ranking prompt.

## 4. Operator preferences (also in Claude memory)

Stocks only, no crypto. Read FREEZE.md before the trading path. Grep WORKLIST
for an idea before testing it. Commit rules before any test runs. One big
model on the GPU. Commit at end of day. Wants depth and real numbers; prefers
own stock picks now (core list). No phone alerts.

## Useful commands

    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1   # health check
    uv run pytest -q                                                 # 1100 tests
    python scripts/watch_list.py                                     # view any list
    python scripts/tradingagents_pass.py --mode premarket --dry-run  # TA queue
    python scripts/momentum_rebalance.py --preview                   # engine picks
    <GPU python> research_gpu/factor_lab.py                          # GPU lab

---

# Handoff — 2026-10-05 (Monday, 09:50 AK)

Branch `momentum-swing-research`, clean at `8211779`. **995 tests green.**
Paper account only, $102,041 equity, **flat** (no positions, no working orders).

**Policy fingerprint `69da227b506e`, window 15** (FREEZE.md). Window 15 has
**zero completed trades** — it was re-declared three times on 10-04/10-05
for genuine defects while empty. **From here, changes to the trading path
wait for window 16 (after 2026-12-04).** New ideas go to the research queue.

---

## After a restart

    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1

Must say ALL CLEAR: Ollama + `gemma4:12b` present, 10 tasks Ready, `.env`,
kill switch clear, Alpaca reachable. It also sets `OLLAMA_CONTEXT_LENGTH=24576`
and `OLLAMA_NUM_PARALLEL=2` when it has to start Ollama.

**Claude Code — Alpaca docs MCP server.** `.mcp.json` (committed) adds
`alpaca-us` = `https://docs.alpaca.markets/mcp?projects=alpaca-us`: Alpaca's
API documentation, read-only, no keys, cannot see the account or trade.
`claude mcp list` shows it **Pending approval** -- start `claude` in this
folder and **approve it when asked** (once). First use: confirm the real-time
SIP plan details (Algo Trader Plus) before the window-16 data-feed switch.
`MCP_DOCKER` fails to connect (Docker not running) -- unrelated, harmless.

## 1. What runs (all times Alaska; AK = ET − 4)

| task | when | does |
|---|---|---|
| `stock-scanner-scan` | 05:33–12:03 every 30 min, 7 days | scan → gates → news gate → bracket order; position review |
| `stock-scanner-advisor-pre` | 03:30 weekdays | TradingAgents rates held + last slate (record-only) |
| `stock-scanner-advisor-live` | :06/:36, 05:36–11:36 weekdays | TradingAgents between scans; pauses at :02/:32 |
| `stock-scanner-flatten` | **weekdays** 08:45, 08:52, 11:45, 11:52 | acts only on the last session before a 3+ day closure |
| `stock-scanner-sector` | 05:00 | sector medians → `data/sector_rs.json` |
| `stock-scanner-data` / `outcomes` / `news` | 13:00 / 13:30 / 14:00 | bars, realized outcomes + `score_gemma.py`, news archive |
| `stock-scanner-extended` / `extras` | 18:00 / 19:00 | extended-hours bars, news history + auctions |

`MorningRead` (old LLM-Quant-Engine PDF) is **disabled** at the user's request.

## 2. Changed 2026-10-04/05 (all committed)

**Model.** gemma3:12b → **gemma4:12b**, `SCANNER_LLM_THINK=false`,
`SCANNER_TRIAGE_NUM_CTX=24576` (.env). Gate benchmark 14/14 (was 12/14),
1.45 s/case. Ollama 0.35.1, auto-update OFF, LAN exposure OFF, desktop-app
context set to 24576 (it overrides the env var). Only `gemma4:12b` and
`nomic-embed-text` remain installed — **one big model only, or the PC crashes.**

**Trading path (each fixed while window 15 was empty):**
- **Flatten** waits for cancels to land before closing (INTC 10-02 bug).
- **Flatten is calendar-aware**: weekdays, acts before any 3+ day closure
  (covers holiday Fridays: 12-24, 12-31, 2027-03-25, 06-17), retries at :52,
  writes `FLATTEN_ALERT.txt` to the Desktop if still not flat.
- **Late-signal guard** (`max_entry_drift_pct=0.5`): a setup seen a scan late
  (Yahoo lag) whose price is already 0.5% below entry is skipped — APP and
  PSX filled beside their stops this way.
- **Entry limit +0.30% above the signal** (`entry_limit_offset_pct=0.3`):
  at-signal limits filled 91% and missed net winners (META, LULU, SLB touched
  but never filled). +0.30% fills ~99%, best total R, checked against Yahoo lag.

**Advisory layer.** TradingAgents (TauricResearch, own venv at
`C:\Users\kitti\TradingAgents`) records 5-tier ratings in the audit log as
`tradingagents_rating`; `score_gemma.py` scores them daily. Record-only.
Position review now skips closed markets; its memory shows only the current
model's calls. Audit rows carry `model`.

## 3. Today, 2026-10-05 — window 15, day one

- 03:30 TradingAgents: INTC **Overweight**, TSCO **Overweight**.
- INTC (window 14): the queued Sunday sell was cancelled by the operator
  overnight; `protect.py` re-armed the stop at 06:03, filled 115.85 → **−1.0R**.
- SLB entry sent 06:34, never filled (before the +0.30% change), cancelled
  stale 08:03. TradingAgents rated SLB Hold.
- Every task `0x0`. Only ~1 fresh setup so far — normal (1–2/day).

## 4. What the research says (WORKLIST §26–29, all on 2y Alpaca SIP)

**The live strategy has no proven edge.** With costs and the Friday flatten,
2024-09 → 2026-09: −0.024R/trade, account **−11.6%** (DD 23.9%). Backtests via
`resolve()` never applied the Friday flatten (−0.063R/trade overstatement).

**Nulls:** model take/skip (p=0.42), model per-stock exit plans (**worse**,
p=0.021), model bar-by-bar exits (never acts), indicator layers (MACD positive
on 98.6% of setups), trailing/structure exits, stop buffer, 5-day sector RS,
gap reversion, position cap 30% (worse), all sizing schemes (`SIZING.md`).

**Candidates for window 16 — need the unseen 2018–2024 data first:**
1. `min_reward_risk` 2.0 (the 1.5–2R band loses in both halves)
2. sector leadership (p=0.030)
3. fast daily movers (daily ATR ≥ 4.8%) — hint only
4. positive news before a setup (both halves, not pre-registered)
5. MPC breakout→retest→confirm (−0.05R/trade, steadier; account +5.3% by slot luck)
6. momentum engine (+2.68%/hold, Newey-West t 1.89) — separate engine
7. insider buying before a setup (data collected)
8. short volume into the sweep (data collected)

**Model's role:** reads text well (news triage 14/14, labels 2,421 headlines in
15 min), judges numbers badly. Keep it on the news gate; drop the record-only
ranking and the always-hold position review at window 16.

## 5. Data now on disk (data/ is gitignored)

| store | content |
|---|---|
| `data/intraday_alpaca` | 30m SIP, 2024-09 → 2026-09, 501 names (where ideas were found) |
| **`data/intraday_alpaca_2018`** | **30m SIP, 2018 → now, 501 names, ~8.7y — UNSEEN, for Gate 1** |
| `data/short_volume/` | FINRA daily short volume, 2018-08 → now, 2,052 days |
| `data/insiders/form4.csv` | SEC Form 4 purchases/sales, 2018 → now, 205,650 rows |
| `data/news_history/` | point-in-time headlines, 2019 → |
| `data/news_labels.jsonl` | gemma4 labels for 2,421 headlines |
| `data/broker_archive/` | dated Alpaca order/account snapshots |

## 6. Next session, in order

1. **Gate 1 (GO_LIVE.md): run the baseline and all 8 candidates on
   `data/intraday_alpaca_2018`** (2018 → 2024-08, never used for discovery),
   with the Friday flatten and costs, including 2020 and 2022. The single most
   important result for the funding decision.
2. Review `GO_LIVE.md` (DRAFT): the operator should set the numbers (25%
   starter slice, 3/6/15% stops) before results arrive.
3. Friday 10-09: watch the first calendar-aware flatten (11:45/11:52 AK).
4. FRED key: operator signs up at fred.stlouisfed.org → `.env FRED_API_KEY`.
5. Before real money: Alpaca real-time SIP ($99/mo) for live bars — live reads
   Yahoo while every backtest reads SIP (GO_LIVE Gate 3).

## 7. Operator preferences (also in Claude memory)

- **Stocks only — no crypto.**
- Read FREEZE.md before touching the trading path.
- One big model on the GPU at a time.
- Commit at end of day unless told otherwise.

## Useful commands

    python scripts/trade_timeline.py [--symbol X]   # real Alpaca trades, bar by bar
    python scripts/trade_anatomy.py                  # every trade with context
    python scripts/score_gemma.py                    # model + TradingAgents scorecard
    python scripts/levels.py SYMBOL                  # support/resistance zones
    python scripts/tradingagents_pass.py --dry-run   # what TradingAgents would rate
    python scripts/flatten.py                        # dry run of the weekend flatten

---

# Previous handoff — 2026-08-18


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
