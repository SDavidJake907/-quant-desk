# Quant-desk

A paper-trading system for US stocks that runs itself on one Windows PC, in one Alpaca
**paper** account. Rules decide every pick, price, size, stop and sale. No AI model places
or chooses a trade.

*(The folder, the Python package `scanner` and the `stock-scanner-*` task names keep their
old names on purpose: every task and path points at them. The README as it stood until
2026-10-27, with the full history of Gemma's book, the sweep scanner and the research, is in
`docs/history/README-2026-10-27.md`.)*

## Read this first

**Paper money, and nothing here is proven yet.** Each part below says what it rests on:

- **tested**: held up on data it was not built from
- **chosen**: a reasonable rule, not tested
- **weak**: tested and only marginal
- **failed**: tested and did not hold up

| Part | Evidence |
|---|---|
| SPY core | **tested** by everyone: buy-and-hold S&P paid ~11%/yr since 2007. In October 2026 it beat every picking method here. |
| Rules book (picks by momentum + buy checks) | **chosen**. Momentum on the S&P 500 beat random picks 2016-26 (t 1.49) but was flat once survivorship was removed for 2000-15. |
| Small-cap book | **weak**. S&P 600 momentum beat random by +0.83 pts a 3-month hold (t 1.28) and lost to it 2017-20. |
| ORB day book | **chosen**. Not tested here. |
| Penny watch-list | **shadow only**: records what it would buy, never trades. |
| Gemma (local AI) picking | **failed** here: her chosen names did worse than the ones she passed over (-1.28 pts, t -1.8). Retired 2026-10-26. |

`FREEZE.md` holds every rule change with its reason and evidence, newest at the bottom.
`WORKLIST.md` and `TRIALS.md` hold the ideas already tested. Read them before testing a new
one, because most ideas have been tried.

## The account

```
SPY core        50%   SPY, bought in two halves, topped up below 90% of target.
                      Never sold by a rule, no stop.
Rules book      30%   up to 5 S&P 500 names (`gem-` order ids)
Small caps      10%   up to 4 S&P 600 names, $5+ (`sml-`), only while IWM is above its 200-day
ORB day book    10%   opening-range breakouts (`orb-`), everything sold 10 min before the close
Penny shadow     0%   records its would-buys after the close; never orders
```

Each order's `client_order_id` prefix says which book owns it, and every rule acts only on
its own book.

### How a stock is picked (rules book and small caps)

At 9:45 ET, each picker:

1. builds its universe:
   - rules book: S&P 500, the scanner's price / range / volume floors, $2B+ market cap
   - small caps: S&P 600, $5+, $2M+ traded a day
2. keeps the names that pass every buy check:
   - gate ETF above its 200-day (SPY for the rules book, IWM for small caps)
   - top third of the pool by 12-1 momentum
   - 6-month return up
   - above the 50-day average
   - no earnings within 5 days
   - no more than 2 a sector
   - a name sold in the last 30 days is re-bought only on a new 20-day high
3. ranks the survivors strongest 12-1 momentum first and queues the top ones for the book's
   free slots.

At 10:00 ET, peak watch rechecks each queued name and buys it. The recheck skips a name
that fell 2% since 9:45 or now has earnings inside the buffer. Sizes are equal, and a
volatile name is bought smaller so a stop loses no more than a 3% stop would on a full slot.
The small-cap book buys with cash only, never on margin.

### How a position is sold (peak watch, every 5 minutes; the only seller)

- **Stop**: 2.5 ATR under the buy, held at the broker. It moves to break-even once the
  stock is up 1 ATR. Positions bought before 10-27 keep a 3% cap.
- **Close-based exits** (last 10 minutes of the session):
  - before an earnings report
  - profit lock: once up 5%, a close that gives back half the best gain
  - trailing exit: a close under the highest close less 3 ATR
  - time stop: no new high in 10 trading days and under +1 ATR
- **ORB**: its own stop (the opening-range low) and a 1.5R target. Everything is sold at
  close-10.

The rules never trim a book that is over its size. It simply buys nothing until it's back
under.

## What keeps it safe

| Layer | What it does |
|---|---|
| Broker-side stops | Every position has a live stop at Alpaca, so it holds even when this PC or its internet is down. |
| `protect.py` | Every 30 min: a position without a live stop gets one. The SPY core has none by design. |
| Daily loss stop | No new buys after a 3% day. |
| Kill switch | A file named `STOP_TRADING` in the repo root blocks every new buy. |
| Ownership | Every closer acts only on its own book's prefix. |
| Health check | 06:00 and 14:15 AK: every share covered by a stop, every task's last result, errors in the morning logs. Report in `logs/health/`. |
| Audit log | `logs/trading_audit.jsonl`: every pick, order, refusal and outcome, append-only. |

## The schedule (Alaska time; New York is +4 hours)

| Task | When | Runs |
|---|---|---|
| `stock-scanner-scan` | 05:33-12:03, every 30 min | `protect.py` (stops), `picks_sync.py`, `manage_positions.py` |
| `stock-scanner-gemma` | 05:45 weekdays | `rules_pick.py`, then `small_pick.py` (the name is historical) |
| `stock-scanner-orb` | 05:46-11:56 weekdays | `orb_trade.py` |
| `stock-scanner-peakwatch` | every 5 min, 24/5 | `peak_watch.py`: exits, the 10:00 buys, the SPY core |
| `stock-scanner-health` | 06:00, 14:15 | `health_check.py` |
| `stock-scanner-flatten` | before market closures | `flatten.py` (ORB and the paused sweep book only) |
| `stock-scanner-data` | 13:00 | intraday bars for research |
| `stock-scanner-outcomes` | 13:30 | trade outcomes, standings, the penny shadow, the broker archive |

`scripts/register_tasks.ps1` (and `register_orb.ps1`) register them. The Gemma / AI tasks,
news, briefs and reports retired on 2026-10-26 are disabled, not deleted; their scripts are in
`scripts/archive/`.

## Everyday commands

```powershell
scripts\run_command_center.bat                          # the desk on one HTML page, opened in the browser
.venv\Scripts\python.exe scripts\hub.py                 # one page: what ran, the account, what needs you
.venv\Scripts\python.exe scripts\health_check.py        # the health check, any time (read-only)
.venv\Scripts\python.exe scripts\rules_pick.py          # what the rules book would queue now (dry run)
.venv\Scripts\python.exe scripts\small_pick.py          # same for the small-cap book
.venv\Scripts\python.exe scripts\peak_watch.py --dry-run  # what the exits would do now
.venv\Scripts\python.exe scripts\penny_shadow.py        # record / grade the penny shadow
.venv\Scripts\python.exe scripts\competition.py standings  # books vs the index and random picks
.venv\Scripts\python.exe scripts\protect.py             # which positions lack a stop (report)
.venv\Scripts\python.exe scripts\manual_trade.py NVDA --stop 172.50 --target 195  # preview; --submit sends
```

## Where things live

| Path | What |
|---|---|
| `src/scanner/execution/policy.py` | every rule and size, in one place |
| `src/scanner/execution/satellite.py` | the buy checks and exits |
| `src/scanner/execution/rules_pick.py`, `small_book.py`, `orb.py` | each book's picking rules |
| `src/scanner/execution/plan_run.py` | the 10:00 buys, the exits, the SPY core |
| `src/scanner/ops/penny_shadow.py` | the penny shadow's rules |
| `data/gemma/buy_queue.json`, `data/small/buy_queue.json` | today's queued buys |
| `data/shadow/penny.jsonl`, `logs/shadow/penny.md` | penny shadow records and its grade vs IWM |
| `logs/scheduled_<task>.log` | each task's output; `scheduled_rules_pick.log` has both 9:45 picks |
| `logs/peak_watch/YYYY-MM-DD.md` | every peak watch pass |
| `logs/competition/standings.md` | the books against SPY / IWM and random picks |
| `logs/trading_audit.jsonl` | every decision and order |
| `FREEZE.md` | every rule change, why, and its evidence |
| `WORKLIST.md`, `TRIALS.md` | what has been tested, and the verdicts |

## Changing a rule

Change it in `policy.py` or the rule module, with a test. Add a dated `FREEZE.md` entry before
the first order under it, saying what changed, why, and what it rests on. Judge each book
over months against its index (SPY, or IWM for small caps), not by day.

## Setup

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
Copy-Item .env.example .env      # set SCANNER_SEC_USER_AGENT to your email (SEC needs it)
```

Put Alpaca **paper** keys in `.env`:

```
SCANNER_ALPACA_KEY_ID=...
SCANNER_ALPACA_SECRET_KEY=...
```

This project talks only to the paper endpoint. Then register the tasks once from an
elevated PowerShell: `scripts\register_tasks.ps1` and `scripts\register_orb.ps1`. The health
task has no register script; it runs `scripts\scheduled_health.bat` at 06:00 and 14:15 and was
set up by hand in Task Scheduler.

Tests: `.venv\Scripts\python.exe -m pytest tests -q`
