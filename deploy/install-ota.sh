#!/usr/bin/env bash
# Доустановка обновления по кнопке (OTA) на уже работающий сервер.
#
# Нужна, если сервер разворачивался или обновлялся вручную и шаг «Обновление
# по кнопке» из install.sh не выполнялся. Ставит только части OTA и ничего
# не меняет в самой программе. Запуск: sudo bash /opt/excelkro/deploy/install-ota.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/excelkro}"
DATA_DIR="${DATA_DIR:-/var/lib/excelkro}"
SERVICE_USER="${SERVICE_USER:-excelkro}"
UNIT_NAME="excelkro-update@.service"

if [ "$(id -u)" -ne 0 ]; then
	echo "Нужны права root. Запустите: sudo bash ${APP_DIR}/deploy/install-ota.sh" >&2
	exit 1
fi

for file in "${UNIT_NAME}" "ota-update.sh" "sudoers-excelkro"; do
	if [ ! -f "${APP_DIR}/deploy/${file}" ]; then
		echo "Нет файла ${APP_DIR}/deploy/${file}. Обновите код программы и повторите." >&2
		exit 1
	fi
done

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
	echo "Нет пользователя ${SERVICE_USER}: сначала выполните deploy/install.sh" >&2
	exit 1
fi

echo "1. Папка состояния обновления"
mkdir -p "${DATA_DIR}/update"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}/update"

echo "2. Разовая служба обновления"
cp "${APP_DIR}/deploy/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
chmod 755 "${APP_DIR}/deploy/ota-update.sh"

echo "3. Узкое право sudo"
install -m 440 -o root -g root "${APP_DIR}/deploy/sudoers-excelkro" /etc/sudoers.d/excelkro
if ! visudo -cf /etc/sudoers.d/excelkro; then
	rm -f /etc/sudoers.d/excelkro
	echo "Правило sudo отклонено проверкой и удалено: сервер не тронут." >&2
	exit 1
fi

echo "4. Перечитывание systemd"
systemctl daemon-reload

echo "5. Проверка права от имени службы"
if runuser -u "${SERVICE_USER}" -- sudo -n -l systemctl >/dev/null 2>&1; then
	echo "   Право на запуск службы обновления есть."
else
	echo "   Внимание: проверка права не прошла. Смотрите: sudo -u ${SERVICE_USER} sudo -n -l"
fi

if [ ! -d "${APP_DIR}/.git" ]; then
	echo "   Внимание: ${APP_DIR} — не копия Git, кнопка обновления останется выключенной."
	echo "   Порядок перехода — в docs/09-ota-update.md"
fi

echo "Готово. Откройте страницу «Обновление» и нажмите «Проверить обновления»."
