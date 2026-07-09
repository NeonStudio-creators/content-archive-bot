#!/usr/bin/env bash
# Регистрация systemd-сервиса на Linux VPS
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
SERVICE_PATH="/etc/systemd/system/content-explorer.service"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите с sudo: sudo $0"
  exit 1
fi

sed \
  -e "s|@PROJECT_ROOT@|$ROOT|g" \
  -e "s|@SERVICE_USER@|$USER_NAME|g" \
  "$ROOT/scripts/local/content-explorer.service" > "$SERVICE_PATH"

mkdir -p "$ROOT/logs"
chown -R "$USER_NAME:$USER_NAME" "$ROOT/logs"

systemctl daemon-reload
systemctl enable content-explorer
systemctl restart content-explorer

echo "Сервис content-explorer запущен."
echo "  sudo systemctl status content-explorer"
echo "  tail -f $ROOT/logs/bot.log"