# Открыть .env для редактирования (создаёт из .env.example если нет)
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан .env — заполните токены." -ForegroundColor Yellow
}

notepad .env