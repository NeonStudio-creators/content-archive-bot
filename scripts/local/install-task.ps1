# Автозапуск ContentExplorer при входе в Windows (Task Scheduler)
#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runScript = Join-Path $PSScriptRoot "run.ps1"
$taskName = "ContentExplorer"

$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwsh) {
    $pwsh = Get-Command powershell -ErrorAction SilentlyContinue
}
if (-not $pwsh) {
    Write-Error "Не найден PowerShell"
}

$action = New-ScheduledTaskAction `
    -Execute $pwsh.Source `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`"" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "ContentExplorer Telegram bot (локальный IP для YouTube)" `
    -Force | Out-Null

Write-Host "Задача '$taskName' зарегистрирована — бот стартует при входе в Windows." -ForegroundColor Green
Write-Host "Управление: taskschd.msc → ContentExplorer"