#!/usr/bin/env bash
# Установка на сервер Ubuntu/Debian. Запускать на сервере с sudo.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/excelkro}"
DATA_DIR="${DATA_DIR:-/var/lib/excelkro}"
SERVICE_NAME="excelkro"
SERVICE_USER="excelkro"

echo "1. Системные пакеты"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip rsync git curl sudo

echo "2. Служебный пользователь: ${SERVICE_USER}"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
	sudo useradd --system --home "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "3. Папки приложения и данных"
sudo mkdir -p "${APP_DIR}" "${DATA_DIR}/work" "${DATA_DIR}/update"
sudo chown "$(id -u):$(id -g)" "${APP_DIR}"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"
sudo chmod 750 "${DATA_DIR}"

echo "4. Окружение Python"
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "5. Файл настроек"
if [ ! -f "${APP_DIR}/.env" ]; then
	cp "${APP_DIR}/deploy/.env.example" "${APP_DIR}/.env"
fi
sudo chown "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}/.env"
sudo chmod 640 "${APP_DIR}/.env"

echo "6. Служба systemd"
sudo cp "${APP_DIR}/deploy/app.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo "7. Обновление по кнопке (OTA)"
# Разовая служба обновления и узкое право sudo только на её запуск.
sudo cp "${APP_DIR}/deploy/excelkro-update@.service" "/etc/systemd/system/excelkro-update@.service"
sudo chmod 755 "${APP_DIR}/deploy/ota-update.sh"
sudo install -m 440 -o root -g root "${APP_DIR}/deploy/sudoers-excelkro" /etc/sudoers.d/excelkro
sudo visudo -cf /etc/sudoers.d/excelkro
sudo systemctl daemon-reload
if [ ! -d "${APP_DIR}/.git" ]; then
	echo "   Внимание: ${APP_DIR} — не копия Git, кнопка обновления работать не будет."
	echo "   Порядок перехода на git clone — в docs/09-ota-update.md"
fi

echo "Готово. Проверка: curl http://127.0.0.1:8000/health"
