# Синхронизация .env → Railway Variables (нужен: railway login)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$envFile = Join-Path $ProjectRoot ".env"
Set-Location $ProjectRoot

if (-not (Test-Path $envFile)) {
    Write-Error ".env не найден"
}

$status = railway status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Сначала: railway login" -ForegroundColor Yellow
    railway login
}

$count = 0
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line -notmatch "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") { return }
    $name = $Matches[1]
    $value = $Matches[2]
    Write-Host "Set $name"
    railway variable set "${name}=${value}" --skip-deploys 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Не удалось установить $name"
    } else {
        $count++
    }
}

Write-Host ""
Write-Host "Обновлено переменных: $count" -ForegroundColor Green
Write-Host "Деплой: railway up  (или Redeploy в панели Railway)"