"""OTA-обновление: установка последней версии по кнопке."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .. import __version__

logger = logging.getLogger("excelkro.update")
UNIT = "excelkro-update"
MODES = ("check", "apply")
STATE_NAME = "update-state.json"
LOG_NAME = "update.log"
TIMEOUT = 20
LOG_TAIL = 40
UNIT_FILE = Path("/etc/systemd/system/excelkro-update@.service")
SUDOERS_FILE = Path("/etc/sudoers.d/excelkro")
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


def branch() -> str:
    return os.environ.get("UPDATE_BRANCH", "main").strip() or "main"


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
    if branch() != "main":
        missing.append("разрешена только ветка main")
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
    if mode not in MODES:
        raise UpdateError("Неизвестный режим обновления.")
    if branch() != "main":
        raise UpdateError("OTA разрешён только из ветки main.")
    if not is_git_repo():
        raise UpdateError(NO_GIT)
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
