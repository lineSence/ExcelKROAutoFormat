"""OTA-обновление: установка последней версии по кнопке.

Обновление выполняет отдельная разовая служба systemd `excelkro-update@<режим>`,
которую интерфейс запускает через `systemctl start`:

- режим `check` — только смотрит, есть ли новая версия;
- режим `apply` — забирает код, доставляет зависимости и перезапускает службу.

Отдельная служба нужна по двум причинам. Первая: веб-слой может работать под
ограниченным пользователем и не иметь права писать в свою же папку. Вторая:
при перезапуске процесс веб-слоя умирает, и дочернее обновление оборвалось бы
посередине. Служба systemd живёт в своём окружении и дорабатывает до конца.

Как идёт запуск, зависит от того, под кем работает программа:

- под служебным пользователем — через `sudo` и узкое правило `/etc/sudoers.d/excelkro`;
- от root — напрямую, без `sudo` и без правила.

Сам скрипт обновления служба запускает через `bash`, поэтому право запуска
у файла не требуется: `git reset --hard` при каждом обновлении возвращает
файлам права из репозитория и снимает выставленный вручную флаг `+x`.

Ход работы скрипт пишет в файл состояния и журнал, а интерфейс их читает.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
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

# Где systemd ищет unit-ы и где лежит правило sudo.
UNIT_FILE = Path("/etc/systemd/system/excelkro-update@.service")
SUDOERS_FILE = Path("/etc/sudoers.d/excelkro")

NO_GIT = (
    "Папка программы не является копией Git, поэтому обновлять нечего. "
    "Разверните программу через git clone или обновляйте её вручную."
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
    """Папка состояния обновления: её заполняет скрипт обновления."""
    return Path(os.environ.get("UPDATE_DIR", "/var/lib/excelkro/update"))


def branch() -> str:
    """Ветка, из которой берётся последняя версия."""
    return os.environ.get("UPDATE_BRANCH", "main").strip() or "main"


def unit_name(mode: str) -> str:
    return f"{UNIT}@{mode}.service"


def setup_command() -> str:
    """Команда доустановки частей обновления на сервере."""
    return f"sudo bash {app_dir() / 'deploy' / 'install-ota.sh'}"


def as_root() -> bool:
    """Программа работает от root: sudo и правило ей не нужны."""
    try:
        return os.geteuid() == 0
    except AttributeError:  # не Unix
        return False


def _systemctl() -> str:
    """Полный путь к systemctl: в правиле sudo он указан полностью."""
    return shutil.which("systemctl") or "/usr/bin/systemctl"


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


def parts() -> dict:
    """Что из частей обновления уже установлено на сервере.

    Проверка только читает файловую систему: ничего не запускается.
    Правило sudo нужно только служебному пользователю: под root программа
    запускает службу напрямую, и файла правила на сервере может не быть.

    Право запуска у самого скрипта не проверяется: служба вызывает его
    через `bash`, а флаг `+x` всё равно снимается при каждом `git reset
    --hard` во время обновления.
    """
    script = app_dir() / "deploy" / "ota-update.sh"
    root = as_root()
    unit = UNIT_FILE.is_file()
    sudoers = root or SUDOERS_FILE.exists()
    runnable = script.is_file()
    missing: list[str] = []
    if not unit:
        missing.append("разовая служба excelkro-update@.service")
    if not sudoers:
        missing.append("правило sudo /etc/sudoers.d/excelkro")
    if not runnable:
        missing.append("файл deploy/ota-update.sh")
    return {
        "unit": unit,
        "sudoers": sudoers,
        "script": runnable,
        "root": root,
        "ready": not missing,
        "missing": missing,
        "setup": setup_command(),
    }


def local_commit() -> str:
    """Текущая сборка кода коротким хешем. Чтение ничего не меняет."""
    if not is_git_repo():
        return ""
    code, output = _run(["git", "-C", str(app_dir()), "rev-parse", "--short", "HEAD"])
    return output.splitlines()[0] if code == 0 and output else ""


def running(mode: str) -> bool:
    code, output = _run([_systemctl(), "is-active", unit_name(mode)])
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
        "parts": parts(),
    }


def start(mode: str) -> None:
    """Запускает службу обновления и сразу возвращает управление."""
    if mode not in MODES:
        raise UpdateError("Неизвестный режим обновления.")
    if not is_git_repo():
        raise UpdateError(NO_GIT)
    if running("apply"):
        raise UpdateError("Обновление уже идёт. Подождите его окончания.")

    # Сначала смотрим, всё ли установлено: так сообщение называет причину.
    ready = parts()
    if not ready["ready"]:
        raise UpdateError(
            "Служба обновления не установлена. Не хватает: "
            + "; ".join(ready["missing"])
            + f". Выполните на сервере одну команду: {ready['setup']}"
        )

    # Под root служба запускается напрямую: sudo на сервере может даже не быть.
    command = [_systemctl(), "start", "--no-block", unit_name(mode)]
    if not ready["root"]:
        command = ["sudo", "-n", *command]

    code, output = _run(command)
    if code != 0:
        logger.warning("Служба обновления не запущена: %s", output)
        tail = output.splitlines()[-1] if output else "без пояснений"
        raise UpdateError(
            f"Служба обновления не запустилась: {tail}. Повторите установку "
            f"одной командой: {ready['setup']} — подробности в docs/09-ota-update.md."
        )
