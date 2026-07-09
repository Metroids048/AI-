param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TaskName = "AIQuantPaperEngine"
$EngineScript = Join-Path $Root "scripts\start-paper-engine.ps1"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$EngineScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "AI Quant Paper trading engine (API + 7x24 scheduler)" -Force | Out-Null

Write-Host "Registered logon autostart task: $TaskName"
Write-Host "Manual full console: 一键启动.bat (API + frontend + browser)"
Write-Host "Engine only (advanced): scripts\start-paper-engine.ps1"
Write-Host "Remove with: powershell -File scripts/install-paper-engine-autostart.ps1 -Remove"
