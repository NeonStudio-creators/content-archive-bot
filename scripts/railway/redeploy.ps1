# Остановить локальный бот и задеплоить на Railway
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

Get-Process -Name python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*content-archive-bot*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

$task = Get-ScheduledTask -TaskName "ContentExplorer" -ErrorAction SilentlyContinue
if ($task -and $task.State -eq "Running") {
    Stop-ScheduledTask -TaskName "ContentExplorer" -ErrorAction SilentlyContinue
    Write-Host "Остановлена задача ContentExplorer"
}

Write-Host "Локальный бот остановлен." -ForegroundColor Green
Write-Host ""
Write-Host "Дальше:"
Write-Host "  1. railway login"
Write-Host "  2. .\scripts\railway\sync-env.ps1"
Write-Host "  3. railway up"
Write-Host ""
Write-Host "Или в панели Railway: Worker → Redeploy (Variables уже должны быть)"