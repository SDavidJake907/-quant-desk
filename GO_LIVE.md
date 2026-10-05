# Going live with real money — criteria, decided in advance

**DRAFT for the operator's review, written 2026-10-05.** The operator plans to
fund the account in about six weeks (~2026-11-16). Window 15 ends 2026-12-04.
These criteria are set NOW, before window 15 produces a single trade, so that a
good or bad few weeks cannot write them after the fact. Change them only with a
dated note below explaining why — never quietly, and never after seeing results
that the change would rescue.

## Where things stand (2026-10-05)

- Two years of backtest, with costs and the Friday flatten as live runs it:
  **−0.024R per trade, account −11.6%** (`position_size_test.py`,
  `sizing_research.py`). The last six months were positive (+0.185R); the
  eighteen before them negative.
- Live record: 15 closed trades, inconclusive. Two of the worst were an
  execution bug (late signals), fixed 2026-10-04.
- Open leads, all found on the same two years and so unconfirmed: minimum
  reward:risk 2.0, sector leadership, faster daily movers, and the separate
  momentum engine (t 1.89 after the overlap correction).

**On today's evidence the strategy should not be funded.** The six weeks are
for finding out whether that changes.

## Gate 1 — the strategy holds on data it was not built on

On the 2018-01 → 2024-08 Alpaca store (`data/intraday_alpaca_2018`, collected
2026-10-05; nothing here was found on it), with every live rule, the Friday
flatten and 10bps costs:

- [ ] expectancy per trade > 0
- [ ] account simulation (5 slots, compounding, cash-only) ends positive
- [ ] maximum drawdown under 25%
- [ ] positive in at least two of: 2018-19, 2020, 2021, 2022-23 — so it is not
      one good year carrying the rest

Any window-16 rule (reward:risk 2.0, sector leadership, …) is judged here
first. A rule that only works on 2024-26 is not a rule.

## Gate 2 — live trading behaves like the backtest

Window 15, `9ed12e112119`, by its end or by funding day, whichever is later:

- [ ] at least 20 closed trades (fewer cannot distinguish anything)
- [ ] mean R not below −0.25 — worse than that means live differs from the
      backtest, whatever the backtest says
- [ ] every fill within 0.5% of its limit, or the exception explained
- [ ] the news gate never failed open on a trading day
      (`news_gate_degraded` = 0 in session hours)
- [ ] no position carried over a weekend by a failed flatten

## Gate 3 — the machine is trustworthy

- [ ] two consecutive weeks with no failed scheduled task
      (`logs/scheduled.log`), including at least one Friday flatten
- [ ] `start_all.ps1` ALL CLEAR after a deliberate reboot
- [ ] the kill switch tested: `STOP_TRADING` created, a scan refuses to
      trade, file removed
- [ ] the archive (`archive_broker.py`) and outcome jobs ran every day

## If all three pass

- Fund a **starter slice: 25% of the intended amount.**
- Same rules: 1% declared risk, 20% cap (≈0.43% real risk per trade).
- Hard stops, any one halts trading for a written review before restarting:
  - daily loss 3% (exists: `max_daily_loss_pct`)
  - week loss 6%
  - drawdown 15% from the funded high
- Scale to 50%, then 100%, each only after 30 further trades whose mean R is
  within one standard error of the backtest's.

## If any gate fails

Keep trading paper. Do not fund. Record which gate failed and why below; fix or
research; the next evaluation is the end of the following window.

## Change log

| date | change | why |
|---|---|---|
| 2026-10-05 | drafted | before window 15's first scan |
