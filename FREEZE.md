# Evaluation window — FROZEN

    policy fingerprint   9fe7a6618823
    build                9ea2d7d
    branch               momentum-swing-research
    started              2026-08-18, first scan 05:33 AK
    account              Alpaca paper, opened 2026-08-06 at $100,000

Both identifiers matter. The fingerprint says which **rules** ran; the build SHA
says which **code** ran. They come apart: the partial-bar fix (`f19aeff`)
changed live entry prices materially and touched no policy field. A trade is
part of this experiment only if it carries **both** values.

---

## Stop rules, declared in advance

The window ends when **both** are satisfied:

- **100 completed scanner trades** — entered and exited, attributed to this
  system by `client_order_id`. Manual trades in the same account do not count.
- **12 calendar weeks**, so the sample spans more than one market condition.

A **checkpoint at 30 trades** is for reporting only. Looking is allowed;
stopping is not.

### Optional stopping is forbidden

The window does not end because the numbers got good. Stopping at the moment
results look attractive is how a random walk becomes a strategy — it is the
same failure as picking the best of thirteen ablation variants, which cost this
project a day and produced `+0.429R` on train and `+0.014R` on holdout.

If the window has to end early — a bug, a broker change, a decision to stop —
that is recorded here with the reason and the trade count at the time, and the
sample is reported as **truncated**.

### What "completed" means

Entered and exited. Open positions at the checkpoint are excluded from
expectancy and counted separately, because scoring an open trade at its current
mark selects for whatever is currently winning.

---

## Realistic duration

At roughly 2 setups a day across the index, ~40% average deployment, a
Thursday–Friday wind-down and `fresh_bars = 2` currently admitting almost
nothing, the live rate has been about **0.6 orders a day**. At that rate 100
completed trades is on the order of **eight months**.

That is the honest number and it is uncomfortable. It is also why the
`fresh_bars` investigation in `WORKLIST.md` §8 matters — not to loosen the gate
for more trades, but to find out whether the scarcity is real. If it is, this
strategy simply produces evidence slowly and no amount of impatience changes
that.

---

## What must not change during the window

Anything that alters what gets traded or at what price:

- `TradingPolicy` — any field
- `scripts/trade.py`, `scripts/flatten.py`
- `src/scanner/screens/smc.py`, `features/structure.py`, `features/intraday.py`
- `execution/sizing.py`, `guards.py`, `news_gate.py`, `session.py`, `taper.py`,
  `flatten.py`
- The scheduled tasks and their times

If one of these must change — a genuine bug, not an improvement — the window
**ends**. Record the reason below, report the truncated sample, and start a new
window with the new fingerprint and build.

## What may change freely

- Research scripts and anything under `tests/`
- Documentation
- Analysis tooling that reads the audit log
- Any work on a **separate branch** that is not deployed

Research continues. It just does not reach the trading path until this window
closes.

---

## Reporting

Group by `(policy, build)`. `positions.py` currently pools every trade in the
account, which mixes fingerprints and mixes in the five manual trades — that
split is `WORKLIST.md` §1 and should land before the 30-trade checkpoint.

Report at minimum:

- completed trades, and open trades separately
- expectancy in R, with standard error
- win rate, and the target/stop/timeout split
- realized dollars, scanner trades only
- fill rate — the number the backtest could not measure
- mean slippage, entry and stop, versus intended

Against the benchmark of a **+0.218R validated holdout**. Live expectancy
sitting below that is the expected outcome, not a failure: paper fills have been
optimistic by +1.50 against the stop level, and the backtest assumed entry at a
price a limit order may never get.

---

## Window log

| date | event | trades so far |
|---|---|---|
| 2026-08-18 | window opened at `9fe7a6618823` / `9ea2d7d` | 0 |
