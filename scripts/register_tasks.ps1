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
    "stock-scanner-sector",
    "stock-scanner-extended",
    "stock-scanner-extras",
    "stock-scanner-advisor-pre",
    "stock-scanner-advisor-live"
)

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
Register-ScheduledTask -TaskName "stock-scanner-scan" -Trigger $scanTrigger -Action $scanAction -Settings $settings -Description "SMC scan and paper bracket orders, US market hours (05:33-12:03 AK)" -Force | Out-Null
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
# Two triggers. 08:45 AK = 12:45 ET catches a 13:00 ET early close; 11:45 AK =
# 15:45 ET catches the normal bell. The script asks the broker calendar whether
# the close is within 25 minutes and only the run in the window acts, so on a
# normal Friday the early trigger exits having done nothing.
$flatEarly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 8:45AM
$flatTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At 11:45AM
$flatSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$flatAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_flatten.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-flatten" -Trigger @($flatEarly, $flatTrigger) -Action $flatAction -Settings $flatSettings -Description "Close all positions before the weekend (Friday 11:45 AK = 15:45 ET)" -Force | Out-Null
Write-Output "registered stock-scanner-flatten  Fridays 08:45 + 11:45 AK (early + normal close)"

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

# --- sector strength, pre-open ---------------------------------------------
# 05:00 AK, half an hour before the first scan. Writes today's median sector
# relative strength so the 05:33 scan does not pay 14.42s computing it.
#
# THE TIME IS THE POINT. This first went into the 13:00 AK data job, which
# stamps the file with that day; the next morning's scans ask for the next
# day, miss, and recompute. A job that runs after the data it feeds is no job
# at all.
#
# Advisory only, so unsupervised and never retried: the medians are context
# for the ranking prompt and gate nothing, and the scan computes them itself
# when the file is missing or stale.
$sectorTrigger = New-ScheduledTaskTrigger -Daily -At 5:00AM
$sectorSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$sectorAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_sector.bat" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-sector" -Trigger $sectorTrigger -Action $sectorAction -Settings $sectorSettings -Description "Median sector relative strength, written before the first scan (05:00 AK)" -Force | Out-Null
Write-Output "registered stock-scanner-sector  05:00 AK daily"

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

# --- TradingAgents second opinion, recorded only -----------------------------
# Rates held positions and the latest candidates; appends tradingagents_rating
# to the audit log; sends no order and gates nothing. Weekdays only: on a
# weekend there is no session to have an opinion about.
#
# SHARES THE ONE MODEL WITH THE NEWS GATE. During scan hours a pass stops at
# :02/:32 so the gate has the GPU, and TradingAgents' checkpoint lets the next
# pass resume the same name -- one long run, cut into the gaps between scans.
# Pre-market runs from 03:30 until the 05:32 deadline before the first scan;
# the 2h05 limit outlasts that, so Windows never kills the driver mid-run and
# loses a rating whose checkpoint the finished run already cleared.
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$advSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2 -Minutes 5) -MultipleInstances IgnoreNew
$advPreTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 3:30AM
$advPreAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_tradingagents.bat" -Argument "premarket" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-advisor-pre" -Trigger $advPreTrigger -Action $advPreAction -Settings $advSettings -Description "TradingAgents ratings before the open, recorded only (03:30 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-advisor-pre  03:30 AK weekdays"

# :06/:36 -- three minutes after each scan fires, and the pass waits out a scan
# still running. 05:36 to 11:36: the 12:03 scan is the session's last.
$advLiveSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 28) -MultipleInstances IgnoreNew
$advLiveTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At 5:36AM
$advRepeat = New-ScheduledTaskTrigger -Once -At 5:36AM -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 1)
$advLiveTrigger.Repetition = $advRepeat.Repetition
$advLiveAction = New-ScheduledTaskAction -Execute "$root\scripts\scheduled_tradingagents.bat" -Argument "live" -WorkingDirectory $root
Register-ScheduledTask -TaskName "stock-scanner-advisor-live" -Trigger $advLiveTrigger -Action $advLiveAction -Settings $advLiveSettings -Description "TradingAgents ratings between scans, recorded only (05:36-11:36 AK weekdays)" -Force | Out-Null
Write-Output "registered stock-scanner-advisor-live 05:36-11:36 AK weekdays, every 30 min"

