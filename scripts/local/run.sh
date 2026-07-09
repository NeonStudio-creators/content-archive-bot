#!/usr/bin/env bash
# Запуск ContentExplorer на Linux VPS
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Ошибка: нет .env — сначала ./scripts/local/install.sh"
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Ошибка: нет .venv — сначала ./scripts/local/install.sh"
  exit 1
fi

mkdir -p logs
# shellcheck disable=SC1091
source .venv/bin/activate
exec python main.py 2>&1 | tee -a logs/bot.log