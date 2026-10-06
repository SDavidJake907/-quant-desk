# Prompt registry: every prompt that runs, how they fit together

Updated 2026-10-06. Rule for all of them: **Python computes the numbers; the
model reads text.** No prompt asks the model to compute, predict or repeat a
price. Every model output is schema-bound JSON, and every claim from a
headline must be a verbatim quote, or it is dropped or downgraded.

Model for all: `gemma4:12b`, think off, `num_ctx` 24576, keep-alive 45m, one
model on the GPU (the scanner has priority during market hours).

| # | prompt | file | runs | in | out | read by | authority | frozen |
|---|---|---|---|---|---|---|---|---|
| 1 | **News gate** | `src/scanner/llm/triage.py` | each fresh setup, every scan | company, its headlines + summaries | per article: role, material, category, direction, deal_role, verbatim quote | `execution/news_gate.py` | **blocks** structural news (mergers, being acquired) | yes, scored 14/14 |
| 2 | Candidate ranking | `src/scanner/llm/ranking.py` | each scan with candidates | candidates, headlines, sector standing, policy prose | conviction, driver, concern, veto, quote | audit (`shadow_llm_ranking`) | none (`llm_veto_enabled=False`) | yes; a null (p=0.42), drop at window 16 |
| 3 | **Monitor v2: swing** | `src/scanner/monitor/review.py` via `scripts/review_positions.py` | every scan, only if a new headline appeared | trade math as codes (`monitor/codes.py`), 48h headlines | c H/X, f 0/1/2, k, t (event), e quote, w | audit `position_review`, scorecard | none until TRIALS #45 passes; then level 3 only | no (advisory) |
| 4 | **Monitor v2: momentum** | same prompt via `scripts/momentum_watch.py` | daily, after the close | holding codes (K W S T L M), 48h headlines | same | audit `momentum_review`, `data/momentum/watch.jsonl` | none; Gemma never exits momentum | no |
| 5 | TradingAgents | external repo, `scripts/ta_worker.py` | 03:30 premarket, :06/:36 live; monthly on momentum picks | ticker, date, book | 5-tier rating, trader action, reports | audit `tradingagents_rating`, `data/momentum/ratings.jsonl` | none until TRIALS #44 passes | no (record-only) |
| 6 | Hub narration | `scripts/hub.py --narrate` | by hand | the hub's computed sections | state, summary, watch | the operator | none | no |
| 7 | News labelling (research) | `scripts/news_event_test.py` | by hand | headlines before a setup | event, direction, material | `data/news_labels.jsonl` | none | no |

## One vocabulary

`src/scanner/monitor/events.py` is the single event list for #3, #4, #7, the
TRIALS #45 test and the planned Gemma sentiment factor. #1 keeps its own
coarser categories (frozen, measured, different question).

## Settings that differ, and why

- #3 and #4 send **temperature 0 and seed 7** directly to Ollama: a call that
  will be scored must be reproducible. #1 and #2 use `chat_json` (temperature
  0, no seed) because that client is frozen with the news gate.
- #5 asks Ollama for structured output by **JSON schema** (`ta_worker.py`
  patch): function calling drifted into prose on long prompts.

## Known gaps, queued for the window-16 boundary (frozen files)

- `TradingPolicy.as_prose()` (fed to #2) says "a forced close before the
  weekend"; it is now before any 3+ day closure. It also omits the +0.30%
  entry limit, the late-signal guard and protect.py's breached-stop sale.
- #2 is a measured null; remove it with the sweep strategy.
- TradingAgents (#5) cannot be told the strategy context (stop, hold horizon);
  the momentum report carries it beside the rating instead.
