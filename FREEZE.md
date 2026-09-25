# Evaluation window — FROZEN

    window               6
    policy fingerprint   5b091652a8ab
    code build           add46f1  — the last commit that can change behaviour
    llm environment      gemma3:12b + nomic-embed-text, dedup WORKING
    branch               momentum-swing-research
    started              2026-09-25, first scan 05:33 AK
    first entry possible 2026-09-28  (Friday takes no entries)
    ends                 2026-12-04, fixed in advance — see the stop rules
    account              Alpaca paper, opened 2026-08-06 at $100,000
                         equity $101,862.13 at window open, flat

Window 1 ran 2026-08-18 to 2026-09-24 and was **truncated at 3 trades**. Its
closing record is below; its trades do not pool with these. Windows 2 and 3
were each declared and superseded the same evening, before either ran a single
scan — no scans, no trades, nothing lost. Both restarts bought something worth
more than the nothing they cost: window 2 fixed dedup, window 3 made the
model's ranking falsifiable.

**On the build SHA.** Audit records carry whatever `HEAD` is when the scan
runs, so a documentation commit moves the recorded SHA without changing a line
of executed code. `935c721` is the last commit that touched anything the
trading path can reach; SHAs after it that differ only in documentation pool
with it. When grouping results, check what actually changed between two SHAs
rather than treating every difference as a separate experiment — the point of
recording the build was to catch code changes a fingerprint misses, not to
split the sample every time a file is edited.

Both identifiers matter. The fingerprint says which **rules** ran; the build SHA
says which **code** ran. They come apart: the partial-bar fix (`f19aeff`)
changed live entry prices materially and touched no policy field. A trade is
part of this experiment only if it carries **both** values.

---

## Stop rules, declared in advance

The window ends at **whichever comes first**:

- **2026-12-04**, the last session before 23x5 trading begins on 2026-12-06.
  A hard end, fixed now, before a single trade is collected.
- **100 completed scanner trades** AND **10 calendar weeks** — the original
  pair of rules, kept so a window that somehow fills early still has to span
  time and not only count.

A **checkpoint at 30 trades** is for reporting only. Looking is allowed;
stopping is not.

### Why a fixed end date is not optional stopping

The market changes on 2026-12-06 and a sample spanning it pools two different
environments under one fingerprint. The boundary is therefore a property of the
world, not of the results: it was chosen from a published exchange schedule
before any trade existed, it cannot move, and no outcome can bring it closer.
That is the opposite of stopping when the numbers look good.

### This window will almost certainly end on the date, not the count

2026-12-04 is ten weeks after the first scan. At the rate actually observed —
3 completed trades across 7 scanned days — ten weeks of trading days is on the
order of **20 trades**. That is a checkpoint-sized sample, not an expectancy
measurement, and it is accepted in advance rather than discovered later.

So this window's purpose is narrower than window 1's. It is to collect one
clean, uninterrupted, fully-gated stretch of the current policy in the current
market, and to hand the 23x5 era a known starting point. **It is not expected
to settle whether the strategy works.** Reporting it as though it could would
repeat the error the whole document exists to prevent.

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

**This is why window 3 does not chase 100 trades.** The arithmetic above says
that target is a year or more away, and 2026-12-06 is ten weeks away. Rather
than pretend a ten-week window can reach a sixteen-month goal, window 3 sets
the date as its end and accepts the ~20 trades that come with it. The 100-trade
rule is retained only so it can still bind if the rate turns out far higher
than measured.

The real answer to scarcity is §8, not a longer window.

---

## What must not change during the window

Anything that alters what gets traded or at what price:

- `TradingPolicy` — any field
- `scripts/trade.py`, `scripts/flatten.py`, `scripts/manage_positions.py`,
  `scripts/protect.py`
- `src/scanner/screens/smc.py`, `features/structure.py`, `features/intraday.py`
- `execution/sizing.py`, `guards.py`, `news_gate.py`, `session.py`, `taper.py`,
  `flatten.py`, `safety.py`, `sleeve.py`
- **The LLM layer**: `llm/triage.py`, `llm/ranking.py`, `llm/dedup.py`,
  `llm/ollama.py`, `llm/grounding.py`
- `config.py` — `triage_model`, `embed_model`, `dedup_threshold`,
  `triage_temperature`, `triage_num_ctx`
- `sources/calendar.py` — the earnings gate reads it
- `models.py` — `TradeIntent` is the shape that crosses into execution
- The scheduled tasks, their times, and `scripts/scheduled_*.bat`

### The list was incomplete until 2026-09-24

It named `news_gate.py` but not the LLM modules the gate's verdict comes FROM.
A prompt edit in `triage.py` changes which categories are returned, which
changes which names are blocked, which changes what trades — without touching
a single listed file. `dedup_threshold = 0.92` decides what triage is even
shown. `config.py` names the model.

The same reasoning added `safety.py`, `sleeve.py`, `calendar.py`, `models.py`
and the two position-management scripts, all of which can alter what is held
or at what price. A list that omits them lets the window be changed while
appearing intact, which is worse than having no list.

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
- **news gate status per trade** — this sample is not uniformly gated
- window 3 ends before 2026-12-06 by design, so no trade in it spans the
  23x5 change. If that boundary is ever crossed, split the report there.

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

## News gate coverage is not uniform across this window

The gate consults a local Ollama instance. Ollama does **not** start on boot —
no Run key, nothing in the Startup folder — so after any reboot it is down
until started by hand, and `news_gate.py` fails **open**: every name passes the
news check unrefused. The scan says so loudly (`news_gate_degraded`) and then
trades anyway. LLM triage and dedup fail the same way and are advisory, so they
cost nothing but noise.

This has not been constant. Of the three trades in this window:

| day | trade | news gate |
|---|---|---|
| 2026-08-18 | ACN | degraded — failing open |
| 2026-08-20 | APP | working |
| 2026-09-24 | PSX | degraded — failing open |

**Two of the three in-window trades were taken with no news protection.** Any
report of this sample has to say that, rather than describing a gated strategy.
It is a second reason — beyond n=3 — that these trades cannot yet be read as
evidence about the policy as designed.

Ollama was started by hand on 2026-09-24 and is up now, so scans are gated
until the next reboot. It was deliberately **left on manual start**: automating
it would change the effective filter mid-window, and `WORKLIST.md` §7 already
holds that failing open is acceptable while this is paper, precisely because
the point is to observe the behaviour rather than paper over it. The decision
that actually matters — fail open or fail closed, as a `TradingPolicy` field so
it lands in the fingerprint — belongs before real funds, not here.

This does **not** restart the window. Ollama's availability is not a policy
field and no code changed; the gate behaved exactly as written in both states.
What changes is the reporting obligation, not the experiment.

---

## The market itself changes on 2026-12-06, and this window spans it

The SEC approved Nasdaq's move from 16 to **23 hours a day, five days a week**
in April 2026, with a one-hour break 20:00–21:00 ET for clearing. NYSE Arca and
24X have preliminary approval on the same schedule. It begins **2026-12-06**.
Alpaca already runs its own 24/5 overnight session (20:00–04:00 ET, Blue Ocean
ATS) ahead of it.

**No configuration in this repo is stale because of it.** Regular hours remain
09:30–16:00; the 23-hour day adds sessions *around* the regular session rather
than moving it. `SESSION_OPEN`/`SESSION_CLOSE`, the calendar lookups and the
early-close logic were all checked against the live API and are correct. The
scan window, the bar source (Yahoo, regular hours) and `/v2/clock` are
unaffected — verified at 00:29 ET mid-overnight-session, where the clock still
reports `is_open: false` and `next_open 09:30`.

### The exposure is on overnight holds, and it cannot be configured away

Alpaca's overnight session supports **limit orders only**. Stops are not
offered, so a bracket's `stop_loss` leg cannot act between 20:00 and 04:00.
This is a broker limitation, not a setting left switched off — there is no
value we can change that buys overnight protection.

`bracket_payload` already records that only 54% of these setups resolve inside
one session, so **about half of all positions are held overnight by design**.
Before 2026-12-06 that meant a dark gap: no trading, price discovery resuming
at the bell. After it, those positions sit through ~23 hours of continuous
trading with a stop that is structurally unable to fire until 09:30.

Turning on `extended_hours` would make this worse rather than better: it adds
no protection, and it lets *entries* fill overnight in thin liquidity with no
stop attached. It is deliberately left `False`.

### What this does to the experiment

The policy will not change on 2026-12-06 and neither will the build. The
**market** will. Neither identifier in this document captures that, which is
the same class of problem the build SHA was introduced to solve — a change that
moves results while the fingerprint sits still.

The window is **not** ended for it. But the reporting obligation grows:

- **Trades are reported split at 2026-12-06**, the same way they are grouped by
  `(policy, build)`. Trades either side of that date were taken in structurally
  different markets and do not pool.
- The **+0.218R holdout benchmark predates the change**. It was measured on
  Yahoo regular-hours bars in a market where overnight was dark, so its
  overnight gap distribution may no longer describe live conditions. Comparing
  post-2026-12-06 live results against it is comparing across a regime break.

These dates sit close together and should be read as one sequence:

    2026-12-04   window 3 ends, by prior declaration
    2026-12-06   23x5 trading begins
    2026-12-24   Thursday early close, four-day gap, no flatten (below)
    2026-12-31   Thursday close, four-day gap, no flatten (below)

Window 3 is deliberately placed to end before the first of these rather than
across them. The two December Thursdays fall after it closes, so they are an
operational obligation — flatten by hand — and not a contaminant in the
sample.

---

## Holiday-shortened weeks: the flatten does not run

Two rules are each correct alone and wrong together:

- `wind_down_from_weekday = 4` blocks new entries on **Friday only**, so
  Thursday trades. Its docstring reasons that "Thursday is Thursday whatever
  the month" — deliberately a weekday rule, not a date rule.
- Both flatten triggers are registered **Friday-only** (`DaysOfWeek : 32`,
  confirmed against the live task).

When Friday is a holiday, Thursday is the last session before a multi-day close
and nothing knows it:

| date | day | close | next session | gap | entries | flatten |
|---|---|---|---|---|---|---|
| 2026-11-27 | Fri | 13:00 ET | Mon 11-30 | 3d | blocked | **yes**, 08:45 AK trigger |
| 2026-12-24 | Thu | **13:00 ET** | Mon 12-28 | **4d** | **allowed** | **no** |
| 2026-12-31 | Thu | 16:00 ET | Mon 01-04 | **4d** | **allowed** | **no** |
| 2027-03-25 | Thu | 16:00 ET | Mon 03-29 | **4d** | **allowed** | **no** |
| 2027-06-17 | Thu | 16:00 ET | Mon 06-21 | **4d** | **allowed** | **no** |

**This is not a December problem.** It recurs every time a holiday falls on a
Friday — Christmas, New Year, Good Friday 2027-03-26, Juneteenth observed
2027-06-18 — so roughly four times a year, indefinitely. `MARKET_CALENDAR.md`
lists them, generated by `scripts/market_calendar.py` from the broker's own
calendar; regenerate it when a year is published rather than editing it.

Thanksgiving week is handled correctly — the early-close trigger exists for
exactly that case. The two December Thursdays are not: the system may open
positions on the last session before a four-day close and carry them through
unflattened, which is the precise exposure the flatten was built to prevent.

2026-12-24 is the worse of the two. The decision to let Thursday trade rests on
stated arithmetic — 17 bars available, the flatten costing it −0.313R and it
still landing at +0.467R. On a 13:00 close Thursday has roughly **seven** bars,
so the arithmetic the rule is justified by does not hold that day.

Live code cannot catch this by itself. `is_last_bar_before_weekend` infers the
gap from bar timestamps, which only exist after the close; nothing
forward-looking consults the calendar.

**Mitigation without touching the frozen path:** run `scripts/flatten.py
--submit` by hand on each date above, after 09:00 AK and before the bell. Run
`python scripts/market_calendar.py` to list the dates that still need it. It is idempotent and safe to re-run. A calendar-aware wind-down is the
real fix and is `WORKLIST.md` §10 — it changes `policy.py` and `flatten.py`,
so it cannot land until this window closes.

---

## Window 1 — closed 2026-09-24, TRUNCATED at 3 trades

Ran 2026-08-18 to 2026-09-24 at policy `fb709122e949`, builds `cf0ca28`
through `935c721`. It is reported as **truncated**, per the rule above that an
early end is recorded with its reason and its trade count.

**Result, for the record and not for inference:**

| date | symbol | exit | R | news gate |
|---|---|---|---|---|
| 2026-08-18 | ACN | target | +2.163 | degraded |
| 2026-08-20 | APP | stop | −0.363 | working |
| 2026-09-24 | PSX | stop | −0.471 | degraded |

Mean +0.443R on **n=3**. The standard error on three trades is larger than the
effect being measured, so this number says nothing whatever about the strategy
and must not be quoted as though it did. It is here because the rule says to
report what the window collected, not because it is evidence.

**Why it ended.** No single cause; three that compound:

1. **The host was dark for 30 days** (2026-08-24 to 09-24), 21 trading days
   missed. Recorded below. The calendar clock was extended to compensate,
   which was the right call for that window and is moot now that it is closed.
2. **Two of the three trades were taken with no news protection**, because
   Ollama does not start on boot. A three-trade sample that is also not
   uniformly gated has two independent defects, not one.
3. **23x5 trading begins 2026-12-06**, inside what remained of the window. It
   would have spanned a regime change, and split-reporting a sample this small
   would leave two useless halves instead of one useless whole.

Any one of these would have been survivable. Together they mean the window
could not produce what it was opened to produce, and continuing it would have
spent months accumulating trades into a record already known to be unpoolable.

**Nothing was lost by closing it.** No policy field changed, so the windows
after it run the same rules; the three trades keep their own `(policy, build)` attribution
in the audit log and remain readable as what they are.

---

## A third dimension: the model environment

The fingerprint says which rules ran and the build SHA says which code ran.
Neither says **which models were installed**, and that turns out to matter.

`embed_model` has always pointed at `nomic-embed-text`, and that model had
never been pulled. Ollama answered every embedding request with

    model "nomic-embed-text" not found, try pulling it first

so `pipeline.py` took its documented fallback — `dedup unavailable, continuing
undeduped` — and carried on. **Dedup has therefore never run in this project's
life.** The code was correct, the config named the right model, and the model
was simply absent. Nothing in the fingerprint or the build could have shown it.

It is fixed now, and the fix is not cosmetic. On four headlines — three
phrasings of the same acquisition plus an unrelated dividend — dedup returns
two: the duplicates collapse and the unrelated item survives. Triage now sees
one copy of a story where it used to see three, which changes what the news
gate is shown and therefore which names it can block.

### So the environment is declared here too

A trade belongs to this experiment only if it carries the fingerprint, the
build **and** the model environment above. This is the third identifier, and
like the other two it exists because something moved results while the others
sat still:

| what changed | fingerprint | build | caught by |
|---|---|---|---|
| a policy rule | moves | — | fingerprint |
| the partial-bar fix | still | moves | build SHA |
| Ollama down, gate fails open | still | still | nothing — see the news gate section |
| embedding model absent, dedup dead | still | still | **nothing, for the project's whole life** |

The bottom two rows are the same failure: the model environment is state that
lives outside git, and neither identifier can see it. Checking it is part of
starting a window now, not something to rediscover later.

---

## What window 4 carries that window 3 did not

Window 3 never scanned, so window 4 absorbs an end-to-end audit rather than
needing a window of its own. Five gaps, all closed before the first scan:

1. **`max_hold_bars = 60` was enforced nowhere.** It is how +0.218R was
   measured — 5.6% of holdout trades exited on it — and not one live trade
   ever has. `manage_positions.py` implemented it and was never scheduled. It
   now runs before every scan, refuses to act when the market is shut (it
   closes at market, and the 16:03 ET run is after the bell), records a
   `hold_limit_exit` audit event, and binds to `TradingPolicy.max_hold_bars`
   rather than the backtest's own constant so the two cannot drift apart.

2. **Naked positions were detected and not fixed.** The scan found them,
   shouted, and printed "re-arm with: protect.py --submit" — a manual step in
   an unattended system, and the last time it mattered nobody was watching.
   `protect.py` now runs first on every scan. Deliberately no market-hours
   guard: a protective order sent after the bell is queued for the next open,
   and a stop that activates at the open beats a position naked into it.

3. **This list omitted the LLM layer.** A prompt edit in `llm/triage.py`
   changes which categories come back, which changes which names the gate
   blocks, which changes what trades — without touching a single listed file.
   Corrected above, along with `config.py`, `calendar.py`, `models.py`,
   `safety.py`, `sleeve.py` and the two position scripts.

4. **The triage prompt produced two false blocks.** Measured through the
   production path, not a proxy: `bench_triage.py` runs the real prompt over
   the same cases. It scored 11/14, reading a unit sale (IP) and a dead deal
   (PFE) as `target` — the verdict that refuses a real setup. Three clauses
   were added for deal-is-off, unit-sold and stake-bought. Now 12/14, and
   **false blocks went 2 to 0**. The one new miss, MO as `none` rather than
   `acquirer`, blocks nothing and costs nothing.

5. **Nothing could score the ranking.** `replay_slate.py` resolves recorded
   slates against stored bars, including candidates never bought. Validated
   first on the ten trades whose answer is known: all ten replay with the
   right exit reason, mean difference **−0.172R**, replay pessimistic because
   it assumes the intended entry while live limits filled better. On paper
   that is flattery rather than edge, and replay is the number to plan with.

6. **Headlines were never persisted.** `NewsItem.fetched_at` had always been
   documented as "Point-in-time stamp for backtests" and nothing wrote one to
   disk, so every headline the ranker read was discarded when the scan ended.
   The slate records the model's OUTPUT, which is enough to SCORE its ranking
   — but re-running it, which is what testing a prompt change needs, requires
   the INPUT. That is why the triage prompt edited today could only be
   measured on fourteen hand-written cases.

   `sources/news_store.py` now keeps one append-only file per day, holding
   exactly the items the model was shown, deduplicated on id so fourteen
   scans over the same feed grow the file by stories rather than by scans.
   Verified on live headlines: 60 stored, 0 on a second pass, timestamps
   intact. It never raises — these headlines are evidence ABOUT a decision,
   not an input to it, so a full disk must cost the evidence and never the
   trade.

   The store is written from today forward. Nothing recovers the headlines
   already lost, so the earliest prompt change that can be replayed is the
   next one.

7. **A diagnostic could stop a trade, and an absence could not report
   itself.** The slate writer sat between the funnel and the send loop with
   no guard, so an exception building it would have stopped trading after
   finding setups and before sending orders — and it had never once executed,
   because every scan the day it shipped found zero candidates. It is now a
   function, wrapped, and exercised by a test rather than by the first live
   candidate. `news_store` had been built to never raise on exactly this
   argument and the slate had not been given the same treatment.

   `execution/continuity.py` reports the gap the blackout could not. It
   counts missed SESSIONS rather than elapsed hours, so an ordinary
   overnight and an ordinary weekend are silent while a single missed
   weekday is not. The 2026-08-24 outage would have announced itself on the
   first scan back instead of being found a month later. The check is itself
   wrapped: a diagnostic that crashes the scan is the bug it exists to
   prevent.

**Still open:** the ranking cannot be re-run against anything before
2026-09-25, because that is when the headlines start. And nothing outside
this machine watches it — continuity reports a gap once the host returns,
which is not the same as noticing while it is still dark.

---

## Window 5 superseded: the bar cache renewed its own lease

Window 5 collected nothing — zero records — and is replaced for a data bug
found by asking why a real setup was missed.

**CCL triggered at 11:30 ET on 2026-09-25 and was never traded.** It passed
every gate: price 22.23, dollar volume 38.7M against a 20M floor, reward:risk
2.10, `is_live_setup` true, `crosses_session` false. It sat inside the 2-bar
freshness window for three consecutive scans — 12:03, 12:33 and 13:03 ET —
and the funnel reported `candidates 0` at all three while the setup count
never rose from 79. The scanner never saw it.

### Two faults, and they compounded

**A cache hit renewed its own expiry.** `fetch_intraday` read through
`fetch_payload`, which returns a payload whether it fetched or not, then
called `cache_payload` unconditionally — rewriting the cache with the bytes
just read out of it and stamping a fresh `cached_at`. A stale payload renewed
its lease on every read and could outlive its TTL indefinitely. Measured: a
cache 29 minutes old served bars **three buckets behind** the provider, and
came back stamped 0.0 minutes old.

**The TTL was one full bar, which is also the scan cadence.** A scan thirty
minutes after the last one sat exactly on the expiry boundary, so whether it
saw the newest bar came down to sub-second jitter.

With `fresh_bars = 2`, one bar of lag eats half the life of every setup, and a
setup that triggers while the cache is stale can age out before the scanner
ever sees it. That is the mechanism that loses a CCL.

Fixed by reading the cache explicitly so a hit can be told from a fetch, and
by halving the TTL so the next scheduled scan always refetches. Three tests
pin it. The fingerprint does not move — no policy field changed — which is
precisely the case the build SHA exists for, exactly as with the partial-bar
fix in `f19aeff`.

### What this does not excuse

Scarcity is still real. One setup triggered across 499 symbols today, against
a ten-session mean of **1.9 per session**. The cache bug explains why a rare
setup was also missed; it does not explain why setups are rare. `WORKLIST.md`
§8 still stands.

---

## Window 4 superseded: the dollars were unreadable

Window 4 never scanned. It is replaced because the sizing was measured and
found to be nine times smaller than the policy declared.

Every position was sized by the position cap rather than by risk — the
scanner printed `limited by position cap` on all three trades of window 1 —
and actual risk came out at **0.108%** against a declared `risk_pct` of
**1.0%**:

    ACN   risk $200.12   0.196%    position $7,948   7.80%
    APP   risk  $60.18   0.059%    position $3,789   3.72%
    PSX   risk  $68.70   0.067%    position $3,942   3.87%

Three trades at +0.44R produced $1,860 on a $100,000 account. No drawdown can
be read from that, and no conclusion about fills under size either.

`weekday_deployment_pct` is doubled to `(100, 80, 60, 40, 0)`. Monday now
sits exactly on the existing `max_position_pct` ceiling — `min(20, 100/5)` —
so this is the most the current caps allow without raising one.

### What this does and does not change

**It changes no expectancy.** R is scale-invariant: doubling exposure doubles
dollars and doubles drawdown and leaves the measured +0.248R exactly where it
was. Results from before and after this change pool on R and must NOT be
pooled on dollars.

**`max_positions` was deliberately left at 5.** The obvious move was more
slots, and the arithmetic says it is worse than useless: `position_cap_for` is
`deployment / max_positions`, so ten slots on the same deployment HALVES every
position, and ten slots with doubled deployment is arithmetically identical to
today. The book has never held more than three positions and the cap has never
refused a single candidate — 96 refusals, none of them about slots. Slot count
is decorative while candidates are this scarce; deployment is the real dial.

**Expect the daily-loss guard to bind.** `max_daily_loss_pct = 3.0` is inert
at 0.108% risk per trade and will not stay inert at twice the size. A scan
that refuses to trade after a bad morning is the guard working.

---

## Window 3 superseded: the ranking was unfalsifiable

Window 3 never scanned. It was replaced the same evening because a defect was
found that no amount of running it would have fixed.

**Only bought symbols were recorded.** `TradeIntent` is written when an order
goes out, so every outcome this system can ever observe was already filtered by
rank. The model's third-choice calls never become orders, never become
outcomes, and therefore never become evidence — its ranking could not be shown
wrong by any quantity of live trading. `candidate_refused` did not cover the
gap: it keeps a symbol and a reason, and 89 of 96 read `already holding`, which
is an accounting fact rather than a judgment.

This is survivorship, the same shape as the delisted-history problem in
`WORKLIST.md` §4, and unlike small sample size **it does not shrink with
time**. Ten thousand trades would leave it exactly where it started.

`scripts/trade.py` now writes a `candidate_slate` record on every scan: every
ranked candidate, taken or not, with the model's rank kept apart from the
deterministic one, the levels, and the trigger bar. A candidate ranked third
has a determined outcome sitting in bars already held, so the ranking can be
scored offline against the setups it passed over — no extra trading and no
extra risk. It also means the ~96 days of 30m bars already collected can be
replayed rather than waited for.

**It records and decides nothing.** The fingerprint did not move. But it edits
a file this document lists, so the window ends — which is the rule working as
intended rather than an exception to it.

### Why this was worth a restart and tomorrow would not have been

Window 3 had zero trades and had not run a scan. The restart cost was
literally nothing, and it will never be this cheap again: the first scan is
05:33 AK. Making the change tonight costs an empty window; making it next week
costs a real sample.

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
| 2026-09-24 | news gate coverage recorded as **mixed** — 2 of 3 in-window trades taken ungated. Ollama left on manual start. No restart | 3 |
| 2026-09-24 | **window 1 CLOSED, truncated** — 30-day blackout, 2 of 3 trades ungated, and 23x5 landing inside the remaining term | 3, final |
| 2026-09-24 | **window 2 declared** at `fb709122e949` / `935c721`; hard end 2026-12-04, the last session before 23x5 | 0 |
| 2026-09-24 | **window 2 superseded** before its first scan — `nomic-embed-text` was never installed, so dedup had never run. Pulled; dedup now collapses duplicate stories | 0, no scans |
| 2026-09-25 | **window 3 opened** at `fb709122e949` / `935c721`, gemma3:12b + nomic-embed-text. Same hard end 2026-12-04 | 0 |
| 2026-09-24 | **window 3 superseded** before its first scan — only bought symbols were recorded, leaving the model's ranking unfalsifiable. `candidate_slate` now records the untaken candidates too | 0, no scans |
| 2026-09-25 | **window 4 opened** at `fb709122e949` / `2ac9fac`, after an end-to-end audit closed six gaps and hardened the scan against its own diagnostics. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 4 superseded** before its first scan — sizing measured at 0.108% actual risk against a declared 1.0%, nine times too small to read a drawdown from | 0, no scans |
| 2026-09-25 | **window 5 opened** at `5b091652a8ab`; deployment doubled to (100,80,60,40,0), slots left at 5. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 5 superseded** — the bar cache renewed its own lease and served bars up to three buckets stale; CCL triggered at 11:30 ET, passed every gate, and was never seen | 0, no trades |
| 2026-09-25 | **window 6 opened** at `5b091652a8ab`, same rules, new build. Same hard end 2026-12-04 | 0 |
