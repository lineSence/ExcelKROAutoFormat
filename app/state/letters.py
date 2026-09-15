from __future__ import annotations

import threading

from ..core import mail_store
from ..core.mail import Letter


class LetterStore:
    """Потокобезопасное хранилище писем поверх core.mail_store."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self.items: list[Letter] = []
        self.skipped: list[str] = []

    def restore(self) -> int:
        stored = mail_store.load(self._settings)
        with self._lock:
            self.items = list(stored)
            self.skipped = []
        return len(self.items)

    def remember(self, fresh: list[Letter]) -> int:
        before = {str(item.uid) for item in self.items}
        merged = mail_store.remember(self._settings, list(fresh))
        with self._lock:
            self.items = list(merged)
            self.skipped = []
        return sum(1 for item in fresh if str(item.uid) not in before)

    def note_skipped(self, skipped: list[str] | None = None) -> list[str]:
        """Запоминает причины пропуска последнего захода для страницы почты."""
        values = [str(item) for item in (skipped or []) if str(item).strip()]
        with self._lock:
            self.skipped = values
            return list(self.skipped)

    def save(self) -> None:
        with self._lock:
            snapshot = list(self.items)
        mail_store.save(self._settings, snapshot)

    def by_uid(self, uid: str) -> Letter | None:
        wanted = str(uid or "")
        with self._lock:
            return next((item for item in self.items if str(item.uid) == wanted), None)

    def forget_all(self) -> dict:
        report = mail_store.forget_all(self._settings)
        with self._lock:
            self.items = []
            self.skipped = []
        return report
