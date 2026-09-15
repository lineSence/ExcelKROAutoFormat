"""Память о уже отправленных файлах.

Папка из 1С — не очередь: файл может пролежать там долго, его могут
перезаписать или скопировать второй раз под другим именем. Поэтому
программа помнит не имёна, а sha256 содержимого: один и тот же файл
не уйдёт на сервер дважды, даже если программу перезапустили.

Запись хранит билет разбора, токен готовой сверки и путь к исходному
файлу: после выгрузки исходник надо убрать в папку «сданное».
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("companion.state")

FILE_NAME = "companion-jobs.json"
LIMIT = 500

NEW = "новый"
SENT = "отправлен"
READY = "разобрана"
SAVED = "выгружена"
FAILED = "сбой"


class Store:
    """Список записей по sha256 файла."""

    def __init__(self, folder: Path) -> None:
        self.path = Path(folder) / FILE_NAME
        self._items: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Список заданий не прочитан: %s", self.path)
            return
        rows = data.get("jobs") if isinstance(data, dict) else data
        if isinstance(rows, list):
            self._items = {str(row.get("digest")): dict(row) for row in rows if row.get("digest")}

    def save(self) -> None:
        rows = sorted(self._items.values(), key=lambda row: row.get("updated", 0))[-LIMIT:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".part")
            temporary.write_text(
                json.dumps({"jobs": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.path)
        except OSError:
            logger.warning("Список заданий не сохранён: %s", self.path, exc_info=True)

    def get(self, digest: str) -> dict | None:
        return self._items.get(str(digest or ""))

    def by_token(self, token: str) -> dict | None:
        token = str(token or "")
        for row in self._items.values():
            if str(row.get("token") or "") == token:
                return row
        return None

    def items(self) -> list[dict]:
        return sorted(self._items.values(), key=lambda row: row.get("updated", 0))

    def waiting(self) -> list[dict]:
        """Сверки, по которым ещё ждём разбора."""
        return [row for row in self.items() if row.get("status") == SENT]

    def remember(self, digest: str, **values) -> dict:
        row = self._items.setdefault(str(digest), {"digest": str(digest)})
        row.update({key: value for key, value in values.items() if value is not None})
        row["updated"] = int(time.time())
        self.save()
        return row

    def forget(self, digest: str) -> None:
        self._items.pop(str(digest or ""), None)
        self.save()
