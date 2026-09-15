"""Настройки компаньона: только через интерфейс программы.

В проекте действует правило: значения задаёт человек в интерфейсе, а
не файлы окружения. У компаньона своих страниц нет, поэтому
интерфейс — это окно «Настройки» из трея, а значения лежат в
`companion-state.json` — тот же подход, что у `data/runtime.json` на
сервере.

Пароль сервера показывается только хвостом (`mask_password`), как
`mail.mask_secret` на сервере. Пустое поле в окне настроек значит
«оставить как было», а не «стереть».
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger("companion.config")

APP_FOLDER = "ExcelKRO"
FILE_NAME = "companion-state.json"
SECRET_FIELDS = ("auth_password",)


def default_dir() -> Path:
    """Папка для настроек и журналов.

    На Windows — `%LOCALAPPDATA%\\ExcelKRO`, иначе — папка в домашнем
    каталоге. Рядом с exe писать нельзя: там часто нет прав.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
    root = Path(base) if base else Path.home()
    return root / APP_FOLDER


@dataclass
class Settings:
    """Всё, что видит и правит человек в окне настроек."""

    server_url: str = "http://127.0.0.1:8000"
    auth_user: str = ""
    auth_password: str = ""

    inbox: str = ""
    outbox: str = ""
    errors: str = ""
    archive: str = ""

    poll_seconds: int = 20
    stable_seconds: int = 15
    wait_minutes: int = 30
    timeout_seconds: int = 120

    strict: bool = False
    verify: str = "off"
    logic: int = 100

    prev_from_archive: bool = True
    open_browser: bool = True
    log_keep_days: int = 14

    # --- чтение и запись ---

    @classmethod
    def path(cls, folder: Path | None = None) -> Path:
        return (folder or default_dir()) / FILE_NAME

    @classmethod
    def load(cls, folder: Path | None = None) -> "Settings":
        file = cls.path(folder)
        if not file.is_file():
            return cls()
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Настройки не прочитаны: %s", file)
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def save(self, folder: Path | None = None) -> Path:
        """Пишет настройки. Ошибка диска отдаётся текстом выше."""
        file = self.path(folder)
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary = file.with_name(file.name + ".part")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(file)
        return file

    # --- работа с окном настроек ---

    def mask_password(self) -> str:
        """Хвост пароля для показа. Целиком пароль не показываем."""
        value = str(self.auth_password or "")
        if not value:
            return "не задан"
        return f"…{value[-3:]}" if len(value) > 3 else "…"

    def with_form(self, values: dict) -> "Settings":
        """Новые настройки из окна. Пустой пароль — оставить как было."""
        data = asdict(self)
        for item in fields(self):
            if item.name not in values:
                continue
            raw = values[item.name]
            if item.name in SECRET_FIELDS and not str(raw or "").strip():
                continue
            data[item.name] = _coerce(raw, data[item.name])
        return Settings(**data)

    # --- проверка и папки ---

    def troubles(self) -> list[str]:
        """Чего не хватает для работы. Список показывается в окне."""
        troubles: list[str] = []
        if not str(self.server_url or "").strip():
            troubles.append("Не указан адрес сервера.")
        if not str(self.inbox or "").strip():
            troubles.append("Не указана папка со сверками из 1С.")
        if not str(self.outbox or "").strip():
            troubles.append("Не указана папка для готовых сверок.")
        for title, value in (("Папка со сверками", self.inbox),):
            if str(value or "").strip() and not Path(value).is_dir():
                troubles.append(f"{title} не найдена: {value}")
        return troubles

    def folder(self, name: str) -> Path | None:
        """Папка по имени настройки. Не задана — `None`."""
        raw = str(getattr(self, name, "") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Папка не создана: %s", path, exc_info=True)
            return None
        return path


def _coerce(raw: object, sample: object) -> object:
    """Приводит значение из окна к типу настройки."""
    if isinstance(sample, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in ("1", "true", "yes", "on", "да")
    if isinstance(sample, int):
        try:
            return int(float(str(raw).replace(",", ".")))
        except (TypeError, ValueError):
            return sample
    return str(raw if raw is not None else "").strip()
