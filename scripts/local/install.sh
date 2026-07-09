#!/usr/bin/env bash
# Установка ContentExplorer на Linux VPS
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "ContentExplorer · установка"
echo "Папка: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Ошибка: python3 не найден. Установите Python 3.12+."
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $PY_VER"

if [[ ! -d .venv ]]; then
  echo "Создаю .venv ..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if command -v node >/dev/null 2>&1; then
  echo "Node.js: $(node --version)"
else
  echo "Предупреждение: node не найден — для YouTube установите Node.js 20+"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo "Создан .env — заполните токены перед запуском."
else
  echo ".env уже есть"
fi

echo ""
echo "Готово:"
echo "  1. nano .env"
echo "  2. Остановите Railway Worker (тот же TELEGRAM_BOT_TOKEN)"
echo "  3. ./scripts/local/run.sh"
echo "  4. systemd: sudo ./scripts/local/install-service.sh"