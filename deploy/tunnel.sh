#!/usr/bin/env bash
# Туннель до сервера. Приложение слушает только 127.0.0.1.
# Пример: ./deploy/tunnel.sh user@server
# После запуска откройте http://127.0.0.1:8000 в браузере.
set -euo pipefail

TARGET="${1:-}"
LOCAL_PORT="${LOCAL_PORT:-8000}"
REMOTE_PORT="${REMOTE_PORT:-8000}"

if [ -z "${TARGET}" ]; then
	echo "Укажите сервер: ./deploy/tunnel.sh user@server" >&2
	exit 1
fi

exec ssh -N -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" "${TARGET}"
