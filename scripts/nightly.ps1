# Nightly Garmin pull — invoked by Windows Task Scheduler.
# Activates the project venv, runs the incremental pull, logs the result, and
# shows a toast notification if it fails (e.g. tokens expired -> re-bootstrap).
#
# Register (run once, in PowerShell, from the project root):
#   $act = New-ScheduledTaskAction -Execute "powershell.exe" `
#       -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\nightly.ps1`""
#   $trg = New-ScheduledTaskTrigger -Daily -At 6:30am
#   Register-ScheduledTask -TaskName "GarminCoachNightly" -Action $act -Trigger $trg `
#       -Description "Pull Garmin data into the local store"

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("nightly-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

function Show-Toast($title, $msg) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
            [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $texts = $t.GetElementsByTagName("text")
        $texts.Item(0).AppendChild($t.CreateTextNode($title)) | Out-Null
        $texts.Item(1).AppendChild($t.CreateTextNode($msg)) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($t)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Garmin Coach").Show($toast)
    } catch { }  # toast is best-effort; the log is the source of truth
}

"[$(Get-Date -Format o)] Starting nightly pull" | Tee-Object -FilePath $Log -Append

# Python's logging writes to stderr. Under Stop preference, PS 5.1 would treat
# that as a terminating error, so relax it around the native call and merge
# stderr into the log via 2>&1. $LASTEXITCODE is preserved.
$ErrorActionPreference = "Continue"
& $Python -m garmin_coach.ingest.run --nightly 2>&1 | Tee-Object -FilePath $Log -Append
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -eq 0) {
    "[$(Get-Date -Format o)] Nightly pull OK" | Tee-Object -FilePath $Log -Append
} elseif ($code -eq 3) {
    "[$(Get-Date -Format o)] AUTH FAILURE (exit 3)" | Tee-Object -FilePath $Log -Append
    Show-Toast "Garmin Coach: auth expired" "Re-run: python scripts\bootstrap_login.py"
} else {
    "[$(Get-Date -Format o)] Nightly pull FAILED (exit $code)" | Tee-Object -FilePath $Log -Append
    Show-Toast "Garmin Coach: nightly pull failed" "See $Log"
}

exit $code
