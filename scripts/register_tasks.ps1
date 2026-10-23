# Registers the market-hours scan and the off-hours data collector.
#
# Written as a file rather than passed inline because schtasks.exe silently
# ignores /ET on a MINUTE schedule: the task it creates repeats every 30
# minutes FOREVER, firing all night. It reports success either way, and the
# only place the truth shows is "Repeat: Until: Duration: Disabled" in the
# verbose query. The PowerShell cmdlets set a real repetition duration.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Every task this script registers. -Remove walks this list, so a task added
# below and not added here would keep firing daily with no documented way to
# tear it down -- which is what happened to stock-scanner-news.
$tasks = @(
    "stock-scanner-scan",
    "stock-scanner-data",
    "stock-scanner-flatten",
    "stock-scanner-outcomes",
    "stock-scanner-reconcile",
    "stock-scanner-news",
    "stock-scanner-extended",
    "stock-scanner-extras",
    "stock-scanner-momentum",
    "stock-scanner-gemma",
    "stock-scanner-gemma-portfolio",
    "stock-scanner-journal",
    "stock-scanner-weekly",
    "stock-scanner-monday",
    "stock-scanner-weekahead",
    "stock-scanner-gemma-sunday",
    "stock-scanner-peakwatch",
    "stock-scanner-monitor",
    "stock-scanner-gemma-ext"
)

# Retired 2026-10-06 (WORKLIST §37). Unregistered on every run so a machine
# that still has them stops firing them.
#   sector       fed a relative-strength gate removed as null
#   advisor-live 117 ratings, none scoreable, and it shared the GPU with the
#                news gate every 30 minutes
# Retired 2026-10-23 (operator: trim the repo; FREEZE):
#   extsync      hourly after-hours picks sync -- the picks list is empty since
#                Gemma runs the whole account (10-22)
#   advisor-pre  TradingAgents pre-open ratings, disabled since 10-19
#   signals      Pine signals virtual paper, every 5 min (TRIALS #80, 0/66); trades nothing
#   oneshot-*, picks-pause/resume   one-time tasks, disabled
$retired = @("stock-scanner-sector", "stock-scanner-advisor-live",
    "stock-scanner-extsync", "stock-scanner-advisor-pre", "stock-scanner-signals",
    "stock-scanner-oneshot-fast-exit-1023", "stock-scanner-oneshot-picks-exit-1026",
    "stock-scanner-oneshot-wdc-exit", "stock-scanner-picks-pause", "stock-scanner-picks-resume")
foreach ($name in $retired) {
    try {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Output "retired $name"
    } catch { }
}

if ($Remove) {
    foreach ($name in $tasks) {
        try {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Output "removed $name"
        } catch { Write-Output "$name was not registered" }
    }
    exit 0
}

# --- market-hours scan -----------------------------------------------------
# 05:30 Alaska = 09:30 Eastern. Both zones observe DST together, so the offset
# is 4 hours year-round and this needs no seasonal correction.
#
# OFFSET BY THREE MINUTES, ON PURPOSE. Bars close on the hour and the half
# hour. A task firing at :00 races the data provider to finalise the bar it
# needs, and a run at exactly 05:30 reads YESTERDAY's bars because the first
# bar of the session does not close until 06:00. Starting at :33 means every
# scan reads a bar that closed three minutes earlier and is complete.
#
# A full run takes about 3.2 minutes with ten candidates — 26s to scan 501
# symbols, ~16s per candidate for news and triage, 9s to rank them all in one
# call — so the 30-minute window is not close to tight.
$scanTrigger = New-ScheduledTaskTrigger -Daily -At 5:33AM
$repeating = New-ScheduledTaskTrigger -Once -At 5:33AM -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 30)
$scanTrigger.Repetition = $repeating.Repetition

# IgnoreNew matters: a slow scan must never have a second copy started on top
# of it. Two concurrent runs could size two positions against the same free
# sleeve capital, because each reads the account before the other has ordered.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -MultipleInstances IgnoreNew

$scanAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_scan.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-scan" -Trigger $scanTrigger -Action $scanAction -Settings $settings -Description "Safety pass every 30 min, US market hours: protect.py re-arms stops, 60-bar limit on scanner positions (05:33-12:03 AK). Scanner entries paused." -Force | Out-Null
Write-Output "registered stock-scanner-scan   05:33-12:03 AK, every 30 min"

# --- off-hours data collection --------------------------------------------
# One hour after the close. This is what actually keeps the system "loaded
# with the correct data": 30-minute bars accumulate past Yahoo's 60-day
# window, which is the hard ceiling on every backtest here. Bars not saved
# before they age out cannot be recovered later.
$dataTrigger = New-ScheduledTaskTrigger -Daily -At 1:00PM
$dataSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$dataAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_data.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-data" -Trigger $dataTrigger -Action $dataAction -Settings $dataSettings -Description "Grow 30m bar history past Yahoo's 60-day window" -Force | Out-Null
Write-Output "registered stock-scanner-data   13:00 AK daily"

# --- Friday close-out ------------------------------------------------------
# 11:45 Alaska = 15:45 Eastern, fifteen minutes before the bell. Late enough
# that the day has had its chance, early enough that a market order fills and
# a failure still leaves time to close by hand.
#
# This is the only scheduled task that SUBMITS orders to close. It is not
# supervised by watchdog.py, which refuses order submission outright: a process
# that may have reached the broker and died before reading the response has
# unknown position state, and restarting it could sell twice. flatten.py is
# instead safe to re-run by hand, because an already-closed position counts as
# success.
#
# The wind-down that stops NEW positions from Thursday lives in TradingPolicy
# (wind_down_from_weekday) and is enforced by trade.py, not here — so a missed
# run of this task cannot also silently re-enable Thursday and Friday entries.
# EVERY WEEKDAY, FOUR TRIGGERS (2026-10-05). It used to be Fridays only, and
# when Friday is a market holiday the last session is Thursday: nothing
# flattened and positions rode a four-day close (FREEZE.md: 2026-12-24, 12-31,
# 2027-03-25, 06-17). flatten.py --only-before-closure asks the broker calendar
# for the NEXT session and acts only on the last session before a 3+ day
# closure, so on an ordinary Monday-Thursday every trigger exits doing nothing.
#
#   08:45 AK = 12:45 ET   a 13:00 ET early close          08:52  its retry
#   11:45 AK = 15:45 ET   the normal bell                 11:52  its retry
#
# --only-if-closing-within 25 picks the pair for the day's actual close. The
# retry re-runs the same idempotent job: flat already means nothing to do, and
# anything a first run could not close gets a second chance before the bell.
# Still not flat after the retry: flatten.py writes FLATTEN_ALERT.txt to the
# Desktop and logs/.
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$flatTriggers = @("8:45AM", "8:52AM", "11:45AM", "11:52AM") | ForEach-Object {
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $_ }
$flatSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$flatAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_flatten.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-flatten" -Trigger $flatTriggers -Action $flatAction -Settings $flatSettings -Description "Flat before every weekend-length closure: weekdays 08:45/08:52/11:45/11:52 AK, acts only on the last session before a 3+ day close" -Force | Out-Null
Write-Output "registered stock-scanner-flatten  weekdays 08:45/08:52/11:45/11:52 AK, last session before a closure only"

# --- outcome recording -----------------------------------------------------
# Writes back what each closed trade actually produced. The audit log records
# what was DECIDED and never what came of it, and realized R is otherwise
# recomputed from Alpaca's order history, which the broker prunes.
#
# Read-mostly and outside the frozen trading path: it places no order and
# touches no policy, so it cannot influence a decision. Idempotent, so a missed
# day is picked up by the next run.
$outTrigger = New-ScheduledTaskTrigger -Daily -At 1:30PM
$outSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$outAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_outcomes.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-outcomes" -Trigger $outTrigger -Action $outAction -Settings $outSettings -Description "Append realized outcomes to the audit log" -Force | Out-Null
Write-Output "registered stock-scanner-outcomes 13:30 AK daily"

# --- reconciliation verification --------------------------------------------
# Verifies audit-log intents match broker state: lost orders, untracked
# orders, slippage beyond threshold, untracked positions. Read-only, places
# no order, cannot influence a decision.
$recTrigger = New-ScheduledTaskTrigger -Daily -At 2:00PM
$recSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$recAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_reconcile.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-reconcile" -Trigger $recTrigger -Action $recAction -Settings $recSettings -Description "Reconcile audit-log intents to broker state; report lost/untracked orders and slippage (14:00 AK daily)" -Force | Out-Null
Write-Output "registered stock-scanner-reconcile 14:00 AK daily"

# --- news archive -----------------------------------------------------------
# The one thing a weekend can gather. The scanner runs seven days a week and on
# a weekend gathers nothing — no bars print, so Saturday re-reads Friday's
# series and produces zero candidates, measured across 28 weekend scans.
#
# News does not stop, and the scan only stores it for names that became
# candidates, which on most days is none. So every day loses the same thing a
# weekend does, and there is no reason to archive Saturday but not Tuesday.
#
# 14:00 AK = 18:00 ET, after DATA at 13:00 and OUTCOMES at 13:30, so three jobs
# are not competing for the same provider. Read-only of the market, append-only
# to data/news, and nothing in the trading path reads what it writes.
$newsTrigger = New-ScheduledTaskTrigger -Daily -At 2:00PM
$newsSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$newsAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_news.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-news" -Trigger $newsTrigger -Action $newsAction -Settings $newsSettings -Description "Archive point-in-time headlines for the liquid universe" -Force | Out-Null
Write-Output "registered stock-scanner-news    14:00 AK daily"

# --- extended-hours research data -------------------------------------------
# 18:00 AK = 22:00 ET, after the post-market session closes, so the day is
# complete. Writes to data/intraday_extended, which the trading path never
# reads: a 32-bucket session is a different instrument from a 13-bucket one.
#
# Bars not saved cannot be recovered later, and these cost nothing. Measured:
# regular hours are 82.3% of dollar volume, the "London" window 1.4% and Asia
# 0.3%, so this is research data rather than a second market -- but the
# pre-market range and the 09:00 hour that carries 16.6% of volume are real,
# and the regular store cannot see either.
$extTrigger = New-ScheduledTaskTrigger -Daily -At 6:00PM
$extSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$extAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_extended.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-extended" -Trigger $extTrigger -Action $extAction -Settings $extSettings -Description "Extended-hours SIP bars 04:00-19:30 ET into a separate research store (18:00 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-extended 18:00 AK daily"

# --- point-in-time news and auctions ----------------------------------------
# 19:00 AK, after the extended-hours collection at 18:00 so two jobs do not
# race the provider. News back to 2015 is what finally makes the model layer
# testable: ranking.py has never been scored because the live store held two
# days. Auctions give the official open, which the 30m first bar cannot
# separate from the half hour after it.
$xtTrigger = New-ScheduledTaskTrigger -Daily -At 7:00PM
$xtSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$xtAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_extras.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-extras" -Trigger $xtTrigger -Action $xtAction -Settings $xtSettings -Description "Point-in-time news history and auction prints (19:00 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-extras   19:00 AK daily"

$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

# --- Gemma's book: the junior trader (WORKLIST §42) -------------------------
# 05:45 AK weekdays: after the 05:33 safety pass has placed the picks list's
# orders (so those names are visibly taken) and after the 03:30 ratings have
# freed the GPU. Gemma names up to 5 stocks from core.txt; bookorders prices,
# sizes, stops and sends on its 10% sleeve. Market closed -> nothing sent.
$gemTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 5:45AM
$gemSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$gemAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-gemma" -Trigger $gemTrigger -Action $gemAction -Settings $gemSettings -Description "Gemma's book: morning decision on core.txt names, real paper orders on a 10% sleeve (05:45 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-gemma   05:45 AK weekdays"

# --- Gemma's portfolio review ------------------------------------------------
# 03:00 AK daily, half an hour before Gemma's 05:45 decision. Read-only:
REM writes data/brief/portfolio-review.json which Gemma reads as part of her
REM facts block. Advisory only — nothing in it blocks, sizes, or closes a trade.
$portTrigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$portSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$portAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma_portfolio_review.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-gemma-portfolio" -Trigger $portTrigger -Action $portAction -Settings $portSettings -Description "Gemma's portfolio review: open positions, by-book stats, sector exposure, refusals, TA ratings on holdings (03:00 AK daily)" -Force | Out-Null
Write-Output "registered stock-scanner-gemma-portfolio  03:00 AK daily"

# --- the desk officer's evening journal (WORKLIST §42) -----------------------
# 13:45 AK weekdays, after outcomes (13:30). Code checks every task ran and
# logged no error, every position has its stop, and money is not piled into one
# sector across books; Gemma writes it up. Read-only.
$jouTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 1:45PM
$jouSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$jouAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_journal.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-journal" -Trigger $jouTrigger -Action $jouAction -Settings $jouSettings -Description "Desk officer journal: code checks + Gemma write-up, logs/journal (13:45 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-journal 13:45 AK weekdays"

# --- the Friday weekly review (WORKLIST §45) -------------------------------
# Fridays 07:00 AK: after Gemma's 05:45 decision, five hours before the close,
# so the operator can decide what to hold over the weekend. Read-only.
$wkTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 7:00AM
$wkSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$wkAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_weekly.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-weekly" -Trigger $wkTrigger -Action $wkAction -Settings $wkSettings -Description "Weekly review of every position and book vs the S&P 500, logs/weekly (Fridays 07:00 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-weekly  Fridays 07:00 AK"

# --- Monday research (WORKLIST §48) -----------------------------------------
# Mondays 04:30 AK: refresh Gemma's rolling list (committed on its own) and the
# operator's research pack in logs/research/, before Gemma's 05:45 decision.
$moTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 4:30AM
$moSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$moAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_monday.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-monday" -Trigger $moTrigger -Action $moAction -Settings $moSettings -Description "Monday research: Gemma list refresh + operator research pack (Mondays 04:30 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-monday  Mondays 04:30 AK"

# --- daily brief (WORKLIST §52) ----------------------------------------------
# 03:00 AK daily: calendar, news, the President, and which stocks each item
# touches, for Gemma's 05:45 decision and the operator. Before TradingAgents
# takes the GPU at 03:30; the 20-minute limit keeps it out of that slot.
# Three times a day (operator 2026-10-22, 24/5): 03:00 morning, 12:50 afternoon
# (after the close), 18:50 evening (overnight). Gemma reads the newest within 12 h.
$waTrigger = @((New-ScheduledTaskTrigger -Daily -At 3:00AM), (New-ScheduledTaskTrigger -Daily -At 12:50PM), (New-ScheduledTaskTrigger -Daily -At 6:50PM))
$waSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$waAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_weekahead.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-weekahead" -Trigger $waTrigger -Action $waAction -Settings $waSettings -Description "Daily brief: calendar, news, President, news->stocks for Gemma (03:00, 12:50, 18:50 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-weekahead  03:00, 12:50, 18:50 AK"

# --- momentum paper rebalance ------------------------------------------------
# 11:40 AK = 15:40 ET weekdays, ten minutes before Alpaca's 15:50 ET cutoff
# for market-on-close orders. It acts once a month: the first session of a
# month whose tranche is not yet formed; every other day it prints "already
# rebuilt" and exits. A failed send leaves the book unsaved, so the next
# day's run retries, and the fixed mom- client ids stop a double order.
$momTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 11:40AM
$momSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -MultipleInstances IgnoreNew
$momAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_momentum.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-momentum" -Trigger $momTrigger -Action $momAction -Settings $momSettings -Description "Momentum paper book: monthly tranche rebuild, market-on-close (11:40 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-momentum 11:40 AK weekdays, acts once a month"

# --- extended hours (WORKLIST §59) ---------------------------------------------
# Gemma decides once more on Sunday 16:45 AK, in the overnight session.
$gsTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4:45PM
$gsSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$gsAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-gemma-sunday" -Trigger $gsTrigger -Action $gsAction -Settings $gsSettings -Description "Gemma's Sunday-evening decision (16:45 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-gemma-sunday  Sundays 16:45 AK"

# --- 24/5 (operator 2026-10-22) ------------------------------------------------
# Around the clock; each script asks the broker's clock which session it is and
# exits when none is open (Friday night, weekends before Sunday 16:00 AK).
#   peakwatch  every 5 min: READY / SELL, limit orders outside regular hours
#   monitor    :15 and :45; outside regular hours only the :45 pass (Gemma's sell-ifs)
#   gemma-ext  Gemma's decision every 2 hours 13:15-03:15 AK (after-hours, overnight, premarket)
$pwTrigger = New-ScheduledTaskTrigger -Daily -At 12:00AM
$pwTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:00AM -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$pwSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$pwAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_peak_watch.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-peakwatch" -Trigger $pwTrigger -Action $pwAction -Settings $pwSettings -Description "Peak watch: READY/SELL every 5 min, 24/5 (exits when no session is open)" -Force | Out-Null
Write-Output "registered stock-scanner-peakwatch  every 5 min, 24/5"
$mnTrigger = New-ScheduledTaskTrigger -Daily -At 12:15AM
$mnTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 12:15AM -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$mnSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$mnAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_monitor.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-monitor" -Trigger $mnTrigger -Action $mnAction -Settings $mnSettings -Description "Market monitor every 30 min, 24/5; outside regular hours Gemma's sell-ifs on the :45 pass" -Force | Out-Null
Write-Output "registered stock-scanner-monitor  every 30 min, 24/5"
$geTrigger = New-ScheduledTaskTrigger -Daily -At 1:15PM
$geTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At 1:15PM -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Hours 14 -Minutes 5)).Repetition
$geSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$geAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_gemma_ext.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-gemma-ext" -Trigger $geTrigger -Action $geAction -Settings $geSettings -Description "Gemma's book outside regular hours: every 2 h 13:15-03:15 AK, limit orders, skips closed sessions" -Force | Out-Null
Write-Output "registered stock-scanner-gemma-ext  every 2 h 13:15-03:15 AK"
