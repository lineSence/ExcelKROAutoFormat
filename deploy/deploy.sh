#!/usr/bin/env bash
# Отправка кода на сервер и перезапуск службы.
# Пример: ./deploy/deploy.sh user@server
set -euo pipefail

TARGET="${1:-}"
APP_DIR="${APP_DIR:-/opt/excelkro}"
SERVICE_NAME="excelkro"

if [ -z "${TARGET}" ]; then
	echo "Укажите сервер: ./deploy/deploy.sh user@server" >&2
	exit 1
fi

echo "1. Копирование файлов в ${TARGET}:${APP_DIR}"
rsync -az --delete \
	--exclude ".git" \
	--exclude ".venv" \
	--exclude "venv" \
	--exclude "__pycache__" \
	--exclude ".env" \
	./ "${TARGET}:${APP_DIR}/"

echo "2. Обновление зависимостей и перезапуск"
ssh "${TARGET}" "set -e
	if [ ! -d '${APP_DIR}/venv' ]; then python3 -m venv '${APP_DIR}/venv'; fi
	'${APP_DIR}/venv/bin/pip' install --upgrade pip
	'${APP_DIR}/venv/bin/pip' install -r '${APP_DIR}/requirements.txt'
	if [ ! -f '${APP_DIR}/.env' ]; then cp '${APP_DIR}/deploy/.env.example' '${APP_DIR}/.env'; fi
	sudo systemctl restart ${SERVICE_NAME}
	sleep 2
	curl -sS http://127.0.0.1:8000/health"

echo
echo "Готово."
