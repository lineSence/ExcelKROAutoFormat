"""Очередь выгрузок на рабочий компьютер.

Работа идёт так: программа-компаньон на рабочем компьютере
забирает сверку из папки и отдаёт её серверу. Дальше решает
человек: разбирает спорные пары, заявки и шапку на странице
`/result/{токен}`. И только после всех подтверждений он нажимает кнопку
«Выгрузить на компьютер».

Кнопка не отдаёт файл браузеру: сервер стоит за туннелем и сам
достучаться до рабочего компьютера не может. Она ставит сверку в
эту очередь, а компаньон сам приходит за очередью, скачивает готовый
файл и архив снимков и кладёт их в нужную папку.

Очередь живёт в памяти процесса, как и сами сверки: хранить её на диске
бессмысленно — после перезапуска службы готовых файлов всё равно не
останется. Исключений наружу модуль не отдаёт: неизвестный токен —
это `None`, а не ошибка.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

WAITING = "ждёт"
TAKEN = "забирается"
DONE = "выгружено"
FAILED = "сбой"

# Сколько заданий помним и как долго. Сверки живут по сроку
# RESULT_TTL_MINUTES, поэтому запись старше суток — уже мусор.
LIMIT = 50
KEEP_SECONDS = 24 * 3600


@dataclass
class Export:
    """Одно задание на выгрузку."""

    token: str
    name: str = ""
    warehouse: str = ""
    status: str = WAITING
    note: str = ""
    tries: int = 0
    created: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)

    def view(self) -> dict:
        """Задание для страницы и для JSON."""
        return {
            "token": self.token,
            "name": self.name,
            "warehouse": self.warehouse,
            "status": self.status,
            "note": self.note,
            "tries": self.tries,
            "seconds": round(time.monotonic() - self.created, 1),
        }


class ExportStore:
    """Задания на выгрузку. Ключ — токен сверки."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Export] = {}

    def _clean(self) -> None:
        """Убирает старые записи. Вызывается под замком."""
        now = time.monotonic()
        for token in list(self._items):
            if now - self._items[token].updated > KEEP_SECONDS:
                self._items.pop(token, None)
        while len(self._items) > LIMIT:
            oldest = min(self._items.values(), key=lambda item: item.updated)
            self._items.pop(oldest.token, None)

    def request(self, token: str, name: str = "", warehouse: str = "") -> Export:
        """Кнопка на странице сверки: поставить в очередь.

        Повторное нажатие не плодит задания, а возвращает то же самое
        в состояние «ждёт»: человек может пересобрать файл и попросить
        выгрузить его снова.
        """
        token = str(token or "")
        with self._lock:
            item = self._items.get(token)
            if item is None:
                item = Export(token=token)
                self._items[token] = item
            item.name = str(name or item.name)
            item.warehouse = str(warehouse or item.warehouse)
            item.status = WAITING
            item.note = ""
            item.updated = time.monotonic()
            self._clean()
            return item

    def get(self, token: str) -> Export | None:
        with self._lock:
            return self._items.get(str(token or ""))

    def pending(self) -> list[dict]:
        """Что компаньону ещё нужно забрать, старые — впереди."""
        with self._lock:
            items = [x for x in self._items.values() if x.status in (WAITING, TAKEN, FAILED)]
        return [x.view() for x in sorted(items, key=lambda item: item.created)]

    def items(self) -> list[dict]:
        with self._lock:
            items = list(self._items.values())
        return [x.view() for x in sorted(items, key=lambda item: item.created)]

    def _mark(self, token: str, status: str, note: str = "") -> Export | None:
        with self._lock:
            item = self._items.get(str(token or ""))
            if item is None:
                return None
            item.status = status
            if note:
                item.note = str(note)
            item.updated = time.monotonic()
            if status == TAKEN:
                item.tries += 1
            return item

    def take(self, token: str, note: str = "") -> Export | None:
        """Компаньон начал забирать файлы."""
        return self._mark(token, TAKEN, note)

    def done(self, token: str, note: str = "") -> Export | None:
        """Файлы уже на рабочем компьютере."""
        return self._mark(token, DONE, note)

    def failed(self, token: str, note: str = "") -> Export | None:
        """Выгрузка не удалась: задание остаётся в очереди."""
        return self._mark(token, FAILED, note)

    def forget(self, token: str) -> None:
        with self._lock:
            self._items.pop(str(token or ""), None)

    def keep_alive(self, alive) -> int:
        """Убирает задания, чьи сверки уже удалены по сроку.

        `alive` — проверка токена (обычно `jobs.alive`). Передаётся
        аргументом, чтобы очередь не зависела от склада сверок и легко
        проверялась в тестах.
        """
        gone = 0
        for view in self.items():
            if alive(view["token"]) is None:
                self.forget(view["token"])
                gone += 1
        return gone
