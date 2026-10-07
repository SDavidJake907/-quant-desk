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
    "stock-scanner-news",
    "stock-scanner-extended",
    "stock-scanner-extras",
    "stock-scanner-advisor-pre",
    "stock-scanner-momentum",
    "stock-scanner-gemma",
    "stock-scanner-journal"
)

# Retired 2026-10-06 (WORKLIST §37). Unregistered on every run so a machine
# that still has them stops firing them.
#   sector       fed a relative-strength gate removed as null
#   advisor-live 117 ratings, none scoreable, and it shared the GPU with the
#                news gate every 30 minutes
$retired = @("stock-scanner-sector", "stock-scanner-advisor-live")
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

# --- second opinions: TradingAgents, pre-open, advisory only -----------------
# 03:30 AK weekdays. Rates held positions, the momentum picks and
# watchlists/core.txt (~28 names, ~2 min each, done before the 05:30 open).
# ADVISORY - UNPROVEN (WORKLIST §38): every rating is graded by
# second_opinion.py against the S&P over 21 sessions and gates nothing. The
# 30-minute live pass stays retired, so nothing else wants the GPU at 03:30.
$advSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2 -Minutes 5) -MultipleInstances IgnoreNew
$advPreTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 3:30AM
$advPreAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_tradingagents.bat" -Argument "premarket" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-advisor-pre" -Trigger $advPreTrigger -Action $advPreAction -Settings $advSettings -Description "Second opinions: TradingAgents rates held, momentum and core names before the open; advisory, graded (03:30 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-advisor-pre  03:30 AK weekdays (advisory, graded)"

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

# --- the desk officer's evening journal (WORKLIST §42) -----------------------
# 13:45 AK weekdays, after outcomes (13:30). Code checks every task ran and
# logged no error, every position has its stop, and money is not piled into one
# sector across books; Gemma writes it up. Read-only.
$jouTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 1:45PM
$jouSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$jouAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_journal.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-journal" -Trigger $jouTrigger -Action $jouAction -Settings $jouSettings -Description "Desk officer journal: code checks + Gemma write-up, logs/journal (13:45 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-journal 13:45 AK weekdays"

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
