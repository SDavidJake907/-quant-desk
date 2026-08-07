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

if ($Remove) {
    foreach ($name in "stock-scanner-scan", "stock-scanner-data") {
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
