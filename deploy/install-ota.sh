#!/usr/bin/env bash
# Доустановка обновления по кнопке (OTA) на уже работающий сервер.
#
# Нужна, если сервер разворачивался или обновлялся вручную и шаг «Обновление
# по кнопке» из install.sh не выполнялся. Ставит только части OTA и ничего
# не меняет в самой программе. Запуск: sudo bash /opt/excelkro/deploy/install-ota.sh
#
# Пользователя службы скрипт определяет сам по установленному unit-у: на серверах,
# развёрнутых вручную, служба часто работает от root или под другим именем.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/excelkro}"
DATA_DIR="${DATA_DIR:-/var/lib/excelkro}"
UNIT_NAME="excelkro-update@.service"
MAIN_UNIT="/etc/systemd/system/excelkro.service"

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

# 1. Под кем работает основная служба: именно этому пользователю нужно право.
if [ -n "${SERVICE_USER:-}" ]; then
	source_of_user="задан вручную"
else
	SERVICE_USER="$(systemctl show -p User --value excelkro 2>/dev/null || true)"
	source_of_user="из службы excelkro"
	if [ -z "${SERVICE_USER}" ] && [ -f "${MAIN_UNIT}" ]; then
		SERVICE_USER="$(awk -F= '/^[[:space:]]*User[[:space:]]*=/ {print $2}' "${MAIN_UNIT}" | tail -n1 | xargs || true)"
		source_of_user="из ${MAIN_UNIT}"
	fi
	if [ -z "${SERVICE_USER}" ]; then
		SERVICE_USER="root"
		source_of_user="по умолчанию"
	fi
fi

echo "Пользователь службы: ${SERVICE_USER} (${source_of_user})"

if [ "${SERVICE_USER}" != "root" ] && ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
	echo "Пользователя ${SERVICE_USER} нет в системе." >&2
	echo "Укажите его явно: sudo SERVICE_USER=имя bash ${APP_DIR}/deploy/install-ota.sh" >&2
	exit 1
fi

echo "1. Папка состояния обновления"
mkdir -p "${DATA_DIR}/update"
chown -R "${SERVICE_USER}:$(id -gn "${SERVICE_USER}")" "${DATA_DIR}/update"

echo "2. Разовая служба обновления"
cp "${APP_DIR}/deploy/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
chmod 755 "${APP_DIR}/deploy/ota-update.sh"

# Если программа работает не в /opt/excelkro, unit должен знать настоящие пути.
if [ "${APP_DIR}" != "/opt/excelkro" ] || [ "${DATA_DIR}" != "/var/lib/excelkro" ]; then
	sed -i \
		-e "s#/opt/excelkro#${APP_DIR}#g" \
		-e "s#/var/lib/excelkro#${DATA_DIR}#g" \
		"/etc/systemd/system/${UNIT_NAME}"
fi
if [ "${SERVICE_USER}" != "excelkro" ]; then
	sed -i "s/^Environment=SERVICE_USER=.*/Environment=SERVICE_USER=${SERVICE_USER}/" \
		"/etc/systemd/system/${UNIT_NAME}"
fi

echo "3. Право запуска службы"
if [ "${SERVICE_USER}" = "root" ]; then
	rm -f /etc/sudoers.d/excelkro
	echo "   Служба работает от root: правило sudo не нужно."
else
	tmp_rule="$(mktemp)"
	sed \
		-e "s/^excelkro ALL/${SERVICE_USER} ALL/" \
		-e "s/^Defaults:excelkro/Defaults:${SERVICE_USER}/" \
		"${APP_DIR}/deploy/sudoers-excelkro" >"${tmp_rule}"
	if ! visudo -cf "${tmp_rule}"; then
		rm -f "${tmp_rule}"
		echo "Правило sudo не прошло проверку: сервер не тронут." >&2
		exit 1
	fi
	install -m 440 -o root -g root "${tmp_rule}" /etc/sudoers.d/excelkro
	rm -f "${tmp_rule}"
fi

echo "4. Перечитывание systemd"
systemctl daemon-reload

echo "5. Проверка"
if [ "${SERVICE_USER}" = "root" ]; then
	echo "   Права root достаточно."
elif runuser -u "${SERVICE_USER}" -- sudo -n -l >/dev/null 2>&1; then
	echo "   Право на запуск службы обновления есть."
else
	echo "   Внимание: проверка права не прошла. Смотрите: sudo -u ${SERVICE_USER} sudo -n -l"
fi

if [ ! -d "${APP_DIR}/.git" ]; then
	echo "   Внимание: ${APP_DIR} — не копия Git, кнопка обновления останется выключенной."
	echo "   Порядок перехода — в docs/09-ota-update.md"
fi

echo "Готово. Перезапустите программу (systemctl restart excelkro) и откройте страницу «Обновление»."
