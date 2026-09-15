#!/usr/bin/env bash
# OTA-обновление программы. Запускается только службой excelkro-update@<режим>
# под root. Режимы: check (есть ли новая версия), apply (установить её).
#
# Ветка берётся из ${UPDATE_DIR}/selected-branch, выбранной в веб-интерфейсе.
# Состояние пишется в ${UPDATE_DIR}/update-state.json, подробности — в update.log.
# Веб-интерфейс только читает эти файлы.
set -uo pipefail

MODE="${1:-apply}"
APP_DIR="${APP_DIR:-/opt/excelkro}"
UPDATE_DIR="${UPDATE_DIR:-/var/lib/excelkro/update}"
SERVICE_NAME="${SERVICE_NAME:-excelkro}"
BRANCH="${UPDATE_BRANCH:-main}"
BRANCH_FILE="${UPDATE_DIR}/selected-branch"
if [ -r "${BRANCH_FILE}" ]; then
	STORED_BRANCH="$(tr -d '\r\n' <"${BRANCH_FILE}")"
	if [ -n "${STORED_BRANCH}" ]; then BRANCH="${STORED_BRANCH}"; fi
fi
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
OTA_UNIT_SRC="${APP_DIR}/deploy/excelkro-update@.service"
OTA_UNIT_PATH="/etc/systemd/system/excelkro-update@.service"

SERVICE_USER="$(systemctl show -p User --value "${SERVICE_NAME}" 2>/dev/null)"
SERVICE_USER="${SERVICE_USER:-root}"
id -u "${SERVICE_USER}" >/dev/null 2>&1 || SERVICE_USER="root"
SERVICE_GROUP="$(id -gn "${SERVICE_USER}" 2>/dev/null || echo "${SERVICE_USER}")"

STATE="${UPDATE_DIR}/update-state.json"
LOG="${UPDATE_DIR}/update.log"

mkdir -p "${UPDATE_DIR}"
: >"${LOG}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${UPDATE_DIR}" 2>/dev/null || true

STARTED="$(date --iso-8601=seconds)"
FROM=""
TO=""
BEHIND=0

log() {
	echo "[$(date '+%H:%M:%S')] $*" >>"${LOG}"
}

esc() {
	printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/ /g' | tr -d '\r\n'
}

state() {
	local stage="$1" verdict="$2" message="$3" ok="null" finished="null"
	case "${verdict}" in
		ok) ok="true"; finished="\"$(date --iso-8601=seconds)\"" ;;
		fail) ok="false"; finished="\"$(date --iso-8601=seconds)\"" ;;
	esac
	cat >"${STATE}" <<JSON
{
	"mode": "$(esc "${MODE}")",
	"stage": "$(esc "${stage}")",
	"message": "$(esc "${message}")",
	"ok": ${ok},
	"started_at": "${STARTED}",
	"finished_at": ${finished},
	"branch": "$(esc "${BRANCH}")",
	"from": "$(esc "${FROM}")",
	"to": "$(esc "${TO}")",
	"behind": ${BEHIND}
}
JSON
	chown "${SERVICE_USER}:${SERVICE_GROUP}" "${STATE}" "${LOG}" 2>/dev/null || true
}

fail() {
	log "Ошибка: $1"
	state "ошибка" fail "$1"
	exit 1
}

if [ "${MODE}" != "check" ] && [ "${MODE}" != "apply" ]; then
	fail "Неизвестный режим: ${MODE}"
fi

case "${BRANCH}" in
	""|/*|*/|*-|..*|*..*|*[^A-Za-z0-9._/-]*) fail "Недопустимое имя ветки: ${BRANCH}" ;;
esac

if [ ! -d "${APP_DIR}/.git" ]; then
	fail "В ${APP_DIR} нет копии Git: обновлять нечего"
fi

cd "${APP_DIR}" || fail "Нет доступа к ${APP_DIR}"
git config --global --get-all safe.directory | grep -qx "${APP_DIR}" ||
	git config --global --add safe.directory "${APP_DIR}"

state "проверка версии" run "Смотрим, есть ли новая версия в ветке ${BRANCH}"
log "Режим: ${MODE}, ветка: ${BRANCH}, пользователь службы: ${SERVICE_USER}"

git fetch --quiet origin "${BRANCH}" >>"${LOG}" 2>&1 || fail "Не удалось получить ветку ${BRANCH} из Git"
FROM="$(git rev-parse --short HEAD)"
TO="$(git rev-parse --short FETCH_HEAD)"
BEHIND="$(git rev-list --count HEAD..FETCH_HEAD 2>/dev/null || echo 0)"
log "Здесь ${FROM}, в Git ${TO}, отставание ${BEHIND} коммитов"

if [ "${MODE}" = "check" ]; then
	if [ "${BEHIND}" = "0" ]; then
		state "готово" ok "Установлена последняя версия (${FROM})"
	else
		state "готово" ok "Есть новая версия: ${TO} (коммитов впереди: ${BEHIND})"
	fi
	exit 0
fi

if [ "${BEHIND}" = "0" ]; then
	state "готово" ok "Обновлять нечего: уже последняя версия (${FROM})"
	exit 0
fi

state "забираем код" run "Переходим с ${FROM} на ${TO}"
git reset --hard FETCH_HEAD >>"${LOG}" 2>&1 || fail "Не удалось перейти на ${TO}"
chmod +x "${APP_DIR}"/deploy/*.sh 2>/dev/null || true

state "зависимости" run "Ставим пакеты из requirements.txt"
"${APP_DIR}/venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt" >>"${LOG}" 2>&1 ||
	fail "Не установились зависимости. Код уже обновлён до ${TO}"

WANTED_UNIT="$(mktemp)"
sed -e "s/^User=.*/User=${SERVICE_USER}/" -e "s/^Group=.*/Group=${SERVICE_GROUP}/" \
	"${APP_DIR}/deploy/app.service" >"${WANTED_UNIT}"
if ! cmp -s "${WANTED_UNIT}" "${UNIT_PATH}"; then
	log "Обновляем описание службы (пользователь ${SERVICE_USER})"
	cp "${WANTED_UNIT}" "${UNIT_PATH}"
	systemctl daemon-reload >>"${LOG}" 2>&1
fi
rm -f "${WANTED_UNIT}"

if [ "${APP_DIR}" = "/opt/excelkro" ] &&
	[ "${UPDATE_DIR}" = "/var/lib/excelkro/update" ] &&
	[ -f "${OTA_UNIT_SRC}" ] && [ -f "${OTA_UNIT_PATH}" ]; then
	WANTED_OTA="$(mktemp)"
	sed -e "s/^Environment=SERVICE_USER=.*/Environment=SERVICE_USER=${SERVICE_USER}/" \
		"${OTA_UNIT_SRC}" >"${WANTED_OTA}"
	if ! cmp -s "${WANTED_OTA}" "${OTA_UNIT_PATH}"; then
		log "Обновляем описание службы обновления"
		cp "${WANTED_OTA}" "${OTA_UNIT_PATH}"
		systemctl daemon-reload >>"${LOG}" 2>&1
	fi
	rm -f "${WANTED_OTA}"
fi

state "перезапуск" run "Перезапускаем службу ${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}" >>"${LOG}" 2>&1 || fail "Служба не перезапустилась"

for _ in 1 2 3 4 5 6 7 8 9 10; do
	sleep 2
	if curl -fsS --max-time 3 "${HEALTH_URL}" >>"${LOG}" 2>&1; then
		FROM="${TO}"
		BEHIND=0
		log "Готово: версия ${TO}"
		state "готово" ok "Обновлено до ${TO}, служба отвечает"
		exit 0
	fi
done

fail "После перезапуска служба не ответила на ${HEALTH_URL}. Смотрите journalctl -u ${SERVICE_NAME}"
