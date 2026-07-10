# Генерация уникальных токенов Stats API
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$count = 3
if ($args.Count -gt 0) { $count = [int]$args[0] }

$python = if (Test-Path ".venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
$tokens = & $python -c "
from api.auth import generate_api_token
for _ in range($count):
    print(generate_api_token())
"

Write-Host "Уникальные API-токены (Stats API):" -ForegroundColor Cyan
$tokens | ForEach-Object { Write-Host "  $_" }

$joined = ($tokens -join ",")
Write-Host ""
Write-Host "Добавьте в .env / Railway:" -ForegroundColor Yellow
Write-Host "STATS_API_TOKENS=$joined"
Write-Host ""
Write-Host "Запрос:" -ForegroundColor Green
Write-Host "  curl -H `"Authorization: Bearer $($tokens[0])`" `"http://127.0.0.1:8080/api/v1/stats?url=...`""