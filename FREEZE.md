# Evaluation window — FROZEN

    policy fingerprint   fb709122e949
    build                cf0ca28  (window opened at a8acc1d; see the window log)
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
  Extended to **2026-12-10** by the 2026-08-24 scheduler blackout — see that
  section. Dark days do not count toward this rule.

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

**The 20M liquidity floor roughly halves that again.** The scan went from 130
setups to 69 the day it landed, so expect closer to **sixteen months** for 100
completed trades. That is the price of trading only the tier where the edge was
measured, and it was accepted knowingly.

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

## Restart, 2026-08-18

The window was reopened the same day it started. Two bugs, both found before
any trade was collected, so nothing was lost:

1. **The Friday flatten had no timing guard.** `--only-if-closing-within` was
   never actually in `scheduled_flatten.bat` — an earlier patch reported
   success without checking the replacement matched. Both triggers ran
   unconditionally, and the 08:45 AK one is 12:45 ET, so the whole book would
   have been closed mid-session every Friday.
2. **Trades closed outside their own bracket read as open forever.**
   `flatten.py` closes with `DELETE /v2/positions`, which produces exactly that
   shape, so every flattened trade would have been uncountable and the
   100-trade stop rule unreachable.

Either alone would have invalidated the window. This is what the build SHA is
for: neither changed a policy field, and a fingerprint-only record would have
pooled them with what came after.

## DEPLOYMENT HAZARD — read before switching branches

The scheduled tasks run `scripts\*.bat` from the **working directory**. There
is no checkout step, so **whatever branch is checked out is what trades.**
Leaving the tree on a research branch silently deploys research code.

    git branch --show-current      # must be momentum-swing-research

Check it after any research session. This is the one operational rule that no
test can enforce.

## Scheduler blackout, 2026-08-24 to 2026-09-24

The machine was off. The four scheduled tasks never failed — they were never
reached. Scanning stopped mid-session at **08-24 11:03 AK** and did not resume
until **09-24 05:33 AK**. Windows fired missed-trigger catch-ups on 09-23 at
20:17; the flatten's timing guard correctly made it a no-op and no closing
orders were sent.

**21 trading days were missed** (08-25 through 09-23, excluding Labor Day
09-07), of which 17 were entry-eligible once Fridays are removed. No bar data
was lost: the 09-24 collector backfilled the whole gap from the provider's
60-day window, and `data_check.py --intraday` reports no gaps. Had the host
stayed dark another month, that backfill would have been impossible — the
60-day window is the only reason this cost nothing but time.

What was lost is **exposure**, not data. No policy field and no build changed
across the gap, so the window does **not** restart and the two trades collected
before it still pool with the one after.

### The calendar clock is extended, not counted

The 12-week rule exists so the sample spans more than one market condition. 30
dark days contributed no conditions, so counting them would satisfy the rule
with wall-clock time the strategy never traded through. The calendar
requirement is therefore extended by the length of the blackout:

    12 weeks from 2026-08-18      would have ended 2026-11-10
    + 30 days of blackout         ends 2026-12-10

The trade-count rule is untouched — it was always counting trades, not days.

**This is a judgment call made after seeing the outage, which is the shape of
decision this document exists to constrain.** It is allowed only because it
moves the window's end *away* and never closer: extending the clock cannot end
the window early and cannot be used to stop on a good number. The other
reading — counting the dark days — would have finished the window a month
sooner, and that is the direction that needs guarding against.

### There is no watchdog for a dark host

`logs/watchdog.jsonl` runs inside the same machine and cannot report its own
absence. Nothing detected this gap; it was found by reading the log a month
later. Any future gap longer than one trading day is recorded here the same
way, with the same extension.

---

## Window log

| date | event | trades so far |
|---|---|---|
| 2026-08-18 | window opened at `9fe7a6618823` / `9ea2d7d` | 0 |
| 2026-08-18 | **restarted** at `9fe7a6618823` / `180a117` — two bugs, 0 trades collected so nothing lost | 0 |
| 2026-08-18 | **restarted** at `9fe7a6618823` / `8947547` — end-to-end sweep found two more, still 0 trades | 0 |
| 2026-08-18 | **restarted** at `347ff1e34122` — liquidity floor raised 5M -> 20M on a 2.2% permutation result; first change made for an EDGE reason rather than a bug | 0 |
| 2026-08-18 05:27 AK | **window open and running** at `347ff1e34122` / `a8acc1d`; all four tasks Ready, first scan 05:33 | 0 |
| 2026-08-18 | **restarted** at `fb709122e949` — Thursday reopened for entries; Friday still closed. Arithmetic, not weekday edge | 0 |
| 2026-08-24 → 09-24 | **scheduler blackout**, host off, 21 trading days missed. No restart — no policy or build change. Calendar clock extended to 2026-12-10 | 2 before, 3 now |
