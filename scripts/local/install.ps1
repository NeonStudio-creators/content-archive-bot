# Установка ContentExplorer на Windows (домашний ПК)
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

Write-Host "ContentExplorer · установка" -ForegroundColor Cyan
Write-Host "Папка: $ProjectRoot"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python не найден. Установите Python 3.12+ с https://python.org и отметьте Add to PATH."
}

$pyVer = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python: $pyVer"

if (-not (Test-Path ".venv")) {
    Write-Host "Создаю виртуальное окружение .venv ..."
    python -m venv .venv
}

$pythonVenv = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $pythonVenv -m pip install --upgrade pip
& $pythonVenv -m pip install -r requirements.txt

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    Write-Host "Node.js: $(& node --version) (нужен yt-dlp для YouTube)"
} else {
    Write-Warning "Node.js не найден — YouTube fallback может не работать. Установите с https://nodejs.org"
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Создан .env из .env.example — заполните токены перед запуском." -ForegroundColor Yellow
} else {
    Write-Host ".env уже есть"
}

Write-Host ""
Write-Host "Готово. Дальше:" -ForegroundColor Green
Write-Host "  1. Заполните .env (TELEGRAM_BOT_TOKEN, SESSION_TOKEN, YOUTUBE_SESSION_TOKEN ...)"
Write-Host "  2. Остановите Railway Worker с тем же ботом (конфликт polling)"
Write-Host "  3. .\scripts\local\run.ps1"
Write-Host "  4. Автозапуск: .\scripts\local\install-task.ps1"