# Запуск ContentExplorer на Windows
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    Write-Error ".env не найден. Сначала: .\scripts\local\install.ps1"
}

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Нет .venv — сначала: .\scripts\local\install.ps1"
}

$logDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logFile = Join-Path $logDir "bot.log"

Write-Host "ContentExplorer · запуск (лог: $logFile)" -ForegroundColor Cyan
& $python main.py 2>&1 | Tee-Object -FilePath $logFile -Append