#!/usr/bin/env bash
# Установка на сервер Ubuntu/Debian. Запускать на сервере с sudo.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/excelkro}"
SERVICE_NAME="excelkro"

echo "1. Системные пакеты"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip rsync

echo "2. Папка приложения: ${APP_DIR}"
sudo mkdir -p "${APP_DIR}"
sudo chown "$(id -u):$(id -g)" "${APP_DIR}"

echo "3. Окружение Python"
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "4. Файл настроек"
if [ ! -f "${APP_DIR}/.env" ]; then
	cp "${APP_DIR}/deploy/.env.example" "${APP_DIR}/.env"
fi

echo "5. Служба systemd"
sudo cp "${APP_DIR}/deploy/app.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo "Готово. Проверка: curl http://127.0.0.1:8000/health"
