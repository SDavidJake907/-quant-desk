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
    "stock-scanner-sector"
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
