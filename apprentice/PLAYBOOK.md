# Playbook: how work is done in this repo

Written 2026-10-05 for the local model (gemma4:12b) to read before any code
task. The model's weights do not change from watching. What it gets is this
file plus `log.jsonl`, real tasks with their diffs and reasons, retrieved by
similarity. On 2026-10-15 it is tested on past tasks it has not seen: tests
pass or they don't.

## Hard rules: never break these

1. **Never edit the trading path.** That is everything under `src/scanner/`
   that a scan, an order or a flatten can reach, plus `scripts/run_scan.py`,
   `trade.py`, `protect.py`, `flatten.py`, `manage_positions.py` and the
   `scheduled_*.bat` files. The policy is frozen for the evaluation window
   (FREEZE.md). If a task needs one of those files, stop and say so.
2. **Run the tests before saying anything is done:** `uv run pytest -q`.
   All green (995+), or the work is not done. Report failures as they are.
3. **Never commit.** Write the change; the operator reviews and commits.
4. **Stocks only.** No crypto, ever.
5. **Never touch `.env`, keys, or the Alpaca account.** Paper or not.

## How research is done here

- A research script is READ-ONLY and says so in its docstring.
- The docstring states the question, the data, and the rules **fixed before any
  outcome was looked at**. Rules are committed before the script reads data it
  will be judged on. Never tune a threshold after seeing a result.
- Every backtest uses the live gates (price >= $5, $20M per bar, stop >= 1.5%,
  reward:risk >= 1.5), Mon-Thu entries, the **Friday flatten**, and 10bps round
  trip costs. A backtest without the Friday flatten overstates by ~0.06R/trade.
- Split by time: train first, holdout later. A rule counts only if it holds in
  data it was not found on.
- Report the null as plainly as the hit. Most ideas here have been nulls.
- Data: `data/intraday_alpaca` (2024-09 to 2026-09, where ideas were found),
  `data/intraday_alpaca_2018` (2018 to 2024-08, unseen, for Gate 1).

## How code looks here

- Python 3.12, `from __future__ import annotations`, type hints on signatures.
- Scripts start with a docstring: one-line purpose, the usage line, then why.
- Reuse what exists before writing new code: `gemma_graded.collect`,
  `gates_for`, `position_size_test.exit_of` / `simulate`,
  `relative_strength_test.permutation_p`, `scanner.features.history`.
- Windows: worker processes do not see module globals that `main` changed.
  Pass the store path in the job arguments.
- Many CPU workers during market hours (05:30-12:05 AK) slow the live scan:
  use `--workers 6` or less then.
- Match the surrounding code: its comment density, naming, plain wording.

## Commit messages (for the operator)

`<type>: <what changed and why, in plain words>`; types: feat, fix, refactor,
docs, test, chore, perf, research.

## When unsure

Say what is unclear and stop. A wrong edit to this system costs real trades;
a question costs nothing.
