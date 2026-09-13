"""OTA-обновление: установка последней версии по кнопке.

Служба работает под пользователем `excelkro` и намеренно не имеет права
писать в свою же папку, поэтому сама себя обновить не может. Обновление
выполняет отдельная разовая служба systemd `excelkro-update@<режим>`,
которую интерфейс запускает через `sudo systemctl start`:

- режим `check` — только смотрит, есть ли новая версия;
- режим `apply` — забирает код, доставляет зависимости и перезапускает службу.

Отдельная служба нужна и по второй причине: при перезапуске процесс веб-слоя
умирает, и если запустить обновление дочерним процессом, оно оборвётся
посередине. Служба systemd живёт в своём окружении и дорабатывает до конца.

Ход работы скрипт пишет в файл состояния и журнал, а интерфейс их читает.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from .. import __version__

logger = logging.getLogger("excelkro.update")

# Имя шаблонного unit-а разового обновления.
UNIT = "excelkro-update"
MODES = ("check", "apply")
STATE_NAME = "update-state.json"
LOG_NAME = "update.log"
# Короткие команды состояния: долгая работа идёт в службе systemd.
TIMEOUT = 20
LOG_TAIL = 40

NO_GIT = (
    "Папка программы не является копией Git, поэтому обновлять нечего. "
    "Разверните программу через git clone или обновляйте её вручную."
)
NO_UNIT = (
    "Служба обновления не запускается. Проверьте, что установлены "
    "deploy/excelkro-update@.service и правило sudo из deploy/sudoers-excelkro "
    "(см. docs/09-ota-update.md)."
)


class UpdateError(RuntimeError):
    """Ошибка запуска обновления, понятная человеку."""


def app_dir() -> Path:
    """Папка установленной программы (где лежит папка `app`)."""
    value = os.environ.get("APP_DIR", "").strip()
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    """Папка состояния обновления: её заполняет скрипт под root."""
    return Path(os.environ.get("UPDATE_DIR", "/var/lib/excelkro/update"))


def branch() -> str:
    """Ветка, из которой берётся последняя версия."""
    return os.environ.get("UPDATE_BRANCH", "main").strip() or "main"


def unit_name(mode: str) -> str:
    return f"{UNIT}@{mode}.service"


def _run(args: list[str]) -> tuple[int, str]:
    """Запуск короткой команды. Ошибка запуска не валит страницу."""
    try:
        done = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode, output.strip()


def is_git_repo() -> bool:
    return (app_dir() / ".git").exists()


def local_commit() -> str:
    """Текущая сборка кода коротким хешем. Чтение ничего не меняет."""
    if not is_git_repo():
        return ""
    code, output = _run(["git", "-C", str(app_dir()), "rev-parse", "--short", "HEAD"])
    return output.splitlines()[0] if code == 0 and output else ""


def running(mode: str) -> bool:
    code, output = _run(["systemctl", "is-active", unit_name(mode)])
    return code == 0 or output.startswith("activating")


def read_state() -> dict:
    """Состояние последнего запуска. Пустой словарь — запусков не было."""
    path = state_dir() / STATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_log(tail: int = LOG_TAIL) -> str:
    """Последние строки журнала обновления."""
    path = state_dir() / LOG_NAME
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-tail:])


def status() -> dict:
    """Всё, что нужно странице обновления."""
    state = read_state()
    busy = running("apply") or running("check")
    ahead = int(state.get("behind") or 0)
    return {
        "version": __version__,
        "commit": local_commit(),
        "branch": branch(),
        "app_dir": str(app_dir()),
        "git": is_git_repo(),
        "busy": busy,
        "fresh": bool(state) and ahead == 0 and state.get("ok") is True,
        "behind": ahead,
        "state": state,
        "log": read_log(),
    }


def start(mode: str) -> None:
    """Запускает службу обновления и сразу возвращает управление."""
    if mode not in MODES:
        raise UpdateError("Неизвестный режим обновления.")
    if not is_git_repo():
        raise UpdateError(NO_GIT)
    if running("apply"):
        raise UpdateError("Обновление уже идёт. Подождите его окончания.")

    code, output = _run(["sudo", "-n", "systemctl", "start", "--no-block", unit_name(mode)])
    if code != 0:
        logger.warning("Служба обновления не запущена: %s", output)
        raise UpdateError(NO_UNIT)
