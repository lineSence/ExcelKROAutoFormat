"""OTA-обновление: установка выбранной версии по кнопке.

Обновление выполняется отдельной systemd-службой, потому что основной
uvicorn-процесс перезапускается в ходе установки. Этот модуль только проверяет
состояние и запускает именованную службу; сам git/pip/restart выполняет
``deploy/ota-update.sh``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import __version__

logger = logging.getLogger("excelkro.update")
UNIT = "excelkro-update"
MODES = ("check", "apply")
STATE_NAME = "update-state.json"
LOG_NAME = "update.log"
# Имя маленького state-файла, в котором хранится выбранная пользователем ветка.
# Он хранится отдельно от runtime.json: выбор ветки относится к OTA, а не к
# рабочей логике сверки.
BRANCH_NAME = "selected-branch"
TIMEOUT = 20
LOG_TAIL = 40
UNIT_FILE = Path("/etc/systemd/system/excelkro-update@.service")
SUDOERS_FILE = Path("/etc/sudoers.d/excelkro")
# Проверка выполняется до передачи значения shell/git: сама строка может
# содержать только безопасные символы Git ref. Дополнительные проверки ниже
# отсекают path traversal-подобные конструкции и пустые сегменты.
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
NO_GIT = (
    "Папка программы не является копией Git, поэтому обновлять нечего. "
    "Разверните программу через git clone или обновляйте её вручную."
)


class UpdateError(RuntimeError):
    """Ошибка запуска обновления, понятная человеку."""


def app_dir() -> Path:
    value = os.environ.get("APP_DIR", "").strip()
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    return Path(os.environ.get("UPDATE_DIR", "/var/lib/excelkro/update"))


def _valid_branch(value: str) -> bool:
    """Проверить только синтаксис имени ветки, не обращаясь к Git."""
    value = str(value or "").strip()
    return bool(
        value
        and BRANCH_RE.fullmatch(value)
        and ".." not in value
        and not value.startswith(("/", "-"))
        and not value.endswith("/")
    )


def _branch_file() -> Path:
    return state_dir() / BRANCH_NAME


def _read_selected_branch() -> str:
    """Прочитать сохранённую ветку; повреждённый state безопасно игнорируется."""
    try:
        value = _branch_file().read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value if _valid_branch(value) else ""


def branch() -> str:
    """Вернуть выбранную ветку или исторический fallback из окружения/main."""
    return _read_selected_branch() or os.environ.get("UPDATE_BRANCH", "main").strip() or "main"


def available_branches() -> list[str]:
    """Собрать ветки, уже известные локальному Git-клону.

    Мы не делаем сетевой запрос при отрисовке страницы: список строится из
    локальных refs. Обычная кнопка «Проверить обновления» сначала выполняет
    fetch, после чего список на следующем открытии страницы обновится.
    """
    if not is_git_repo():
        return ["main"]
    branches: set[str] = {"main"}
    code, output = _run(
        [
            "git", "-C", str(app_dir()), "for-each-ref",
            "--format=%(refname:strip=3)", "refs/remotes/origin/",
        ]
    )
    if code == 0:
        for item in output.splitlines():
            item = item.strip()
            if _valid_branch(item):
                branches.add(item)
    code, output = _run(["git", "-C", str(app_dir()), "branch", "--format=%(refname:short)"])
    if code == 0:
        for item in output.splitlines():
            item = item.strip()
            if _valid_branch(item):
                branches.add(item)
    # Не теряем выбранную ветку, если она была сохранена раньше, но временно
    # отсутствует в локальном списке refs (например, после очистки кеша refs).
    selected = branch()
    if _valid_branch(selected):
        branches.add(selected)
    return sorted(branches, key=lambda item: (item != "main", item.lower()))


def set_branch(value: str) -> str:
    """Проверить и атомарно сохранить выбор пользователя.

    Проверка существования ветки выполняется именно до записи state. Поэтому
    ошибочный select/POST не может оставить сервер в состоянии «выбрана ветка,
    которой нет».
    """
    value = str(value or "").strip()
    if not _valid_branch(value):
        raise UpdateError("Недопустимое имя ветки.")
    if value not in available_branches():
        raise UpdateError("Выбранная ветка не найдена среди доступных веток Git.")
    state_dir().mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        fd, name = tempfile.mkstemp(prefix=f".{BRANCH_NAME}.", dir=state_dir())
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, _branch_file())
        finally:
            temp.unlink(missing_ok=True)
    finally:
        os.umask(old_umask)
    return value


def unit_name(mode: str) -> str:
    return f"{UNIT}@{mode}.service"


def setup_command() -> str:
    return f"sudo bash {app_dir() / 'deploy' / 'install-ota.sh'}"


def as_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _systemctl() -> str:
    return shutil.which("systemctl") or "/usr/bin/systemctl"


def _run(args: list[str]) -> tuple[int, str]:
    # Используем список аргументов, а не shell=True. Это одновременно проще
    # анализировать и исключает интерпретацию имени ветки как shell-команды.
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode, output.strip()


def is_git_repo() -> bool:
    return (app_dir() / ".git").exists()


def _has_sudoers() -> bool:
    try:
        return SUDOERS_FILE.is_file()
    except OSError:
        return False


def parts() -> dict:
    """Показать, достаточно ли установлено компонентов OTA для запуска."""
    script = app_dir() / "deploy" / "ota-update.sh"
    root = as_root()
    unit = UNIT_FILE.is_file()
    # Для root отдельное sudoers-правило не нужно: он и так может запустить
    # systemd. Для непривилегированного процесса правило обязательно.
    sudoers = root or _has_sudoers()
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
    if not is_git_repo():
        return ""
    code, output = _run(["git", "-C", str(app_dir()), "rev-parse", "--short", "HEAD"])
    return output.splitlines()[0] if code == 0 and output else ""


def running(mode: str) -> bool:
    code, output = _run([_systemctl(), "is-active", unit_name(mode)])
    return code == 0 or output.startswith("activating")


def read_state() -> dict:
    path = state_dir() / STATE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_log(tail: int = LOG_TAIL) -> str:
    path = state_dir() / LOG_NAME
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-tail:])


def status() -> dict:
    """Собрать всё, что нужно странице /update, без запуска самого обновления."""
    state = read_state()
    selected = branch()
    busy = running("apply") or running("check")
    state_branch = str(state.get("branch") or "")
    # `behind` относится только к той же ветке. Иначе после переключения с main
    # на fix/* на странице мог бы остаться старый результат предыдущей проверки.
    behind = int(state.get("behind") or 0) if state_branch in ("", selected) else 0
    return {
        "version": __version__,
        "commit": local_commit(),
        "branch": selected,
        "branches": available_branches(),
        "app_dir": str(app_dir()),
        "git": is_git_repo(),
        "busy": busy,
        "fresh": bool(state) and behind == 0 and state.get("ok") is True and state_branch in ("", selected),
        "behind": behind,
        "state": state,
        "log": read_log(),
        "parts": parts(),
    }


def start(mode: str, selected_branch: str | None = None) -> None:
    """Проверить параметры и запустить разовую systemd-службу OTA.

    Сама функция ничего не обновляет синхронно. `--no-block` позволяет сразу
    вернуть HTTP-ответ, а отдельная служба продолжает работу после перезапуска
    uvicorn.
    """
    if mode not in MODES:
        raise UpdateError("Неизвестный режим обновления.")
    if not is_git_repo():
        raise UpdateError(NO_GIT)
    if selected_branch is not None:
        set_branch(selected_branch)
    selected = branch()
    if not _valid_branch(selected):
        raise UpdateError("Ветка обновления указана некорректно.")
    if running("apply"):
        raise UpdateError("Обновление уже идёт. Подождите его окончания.")
    ready = parts()
    if not ready["ready"]:
        raise UpdateError(
            "Служба обновления не установлена. Не хватает: "
            + "; ".join(ready["missing"])
            + f". Выполните на сервере одну команду: {ready['setup']}"
        )

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
