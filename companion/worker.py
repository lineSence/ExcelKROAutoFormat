"""Работа компаньона: один проход — один `tick`.

Проход делает три вещи по очереди:

1. `send_new` — новые файлы из папки 1С уходят на сервер;
2. `follow` — проверяется ход разбора, готовая сверка зовёт человека;
3. `take_exports` — то, что человек отправил кнопкой, ложится в папку.

Решения принимает человек: сам по себе компаньон никогда не выгружает
сверку — только ту, что уже поставлена в очередь на сервере.

Файл считается готовым к отправке, когда он не менялся последние
`stable_seconds` секунд: 1С копирует файл не мгновенно, а открытый в
Excel файл вообще трогать нельзя (его спутник `~$имя.xlsx` пропускается).
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import time
import webbrowser
from pathlib import Path

from . import state as state_module
from .client import Gone, ServerError

logger = logging.getLogger("companion.worker")

SKIP_PREFIXES = ("~$", ".")
RESULT_KIND = "result"
PHOTOS_KIND = "photos.zip"


def digest_of(path: Path, chunk: int = 1024 * 1024) -> str:
    """sha256 файла: по нему программа отличает новое от уже сданного."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def settled(path: Path, seconds: int, now: float | None = None) -> bool:
    """Файл дописан: время изменения старше выдержки."""
    try:
        changed = Path(path).stat().st_mtime
    except OSError:
        return False
    return ((now if now is not None else time.time()) - changed) >= max(int(seconds), 0)


def pick_files(folder: Path, seconds: int) -> list[Path]:
    """Сверки из папки, готовые к отправке."""
    found: list[Path] = []
    for path in sorted(Path(folder).glob("*.xlsx")):
        if path.name.startswith(SKIP_PREFIXES) or not path.is_file():
            continue
        if settled(path, seconds):
            found.append(path)
    return found


def free_path(folder: Path, name: str) -> Path:
    """Путь в папке, не затирающий чужой файл: «Имя (2).xlsx»."""
    target = Path(folder) / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for number in range(2, 1000):
        candidate = Path(folder) / f"{stem} ({number}){suffix}"
        if not candidate.exists():
            return candidate
    return Path(folder) / f"{stem} ({int(time.time())}){suffix}"


def move_aside(path: Path, folder: Path | None) -> Path | None:
    """Убирает исходный файл из рабочей папки. Папки нет — оставляет."""
    path = Path(path)
    if folder is None or not path.is_file():
        return None
    target = free_path(folder, path.name)
    try:
        shutil.move(str(path), str(target))
    except OSError:
        logger.warning("Файл не перемещён: %s", path, exc_info=True)
        return None
    return target


class Companion:
    """Один проход работы. Всё внешнее приходит аргументами."""

    def __init__(self, settings, client, store, notify=None, browser=None) -> None:
        self.settings = settings
        self.client = client
        self.store = store
        self._notify = notify or (lambda title, text: None)
        self._browser = browser or webbrowser.open

    def say(self, title: str, text: str) -> None:
        logger.info("%s: %s", title, text)
        try:
            self._notify(title, text)
        except Exception:  # noqa: BLE001
            logger.debug("Подсказка не показана", exc_info=True)

    # --- шаг 1: заливка ---

    def send_new(self) -> int:
        folder = self.settings.folder("inbox")
        if folder is None:
            return 0
        sent = 0
        for path in pick_files(folder, self.settings.stable_seconds):
            digest = digest_of(path)
            known = self.store.get(digest)
            if known is not None and known.get("status") != state_module.FAILED:
                continue
            try:
                answer = self.client.upload(path)
            except ServerError as error:
                self.store.remember(digest, name=path.name, source=str(path), status=state_module.FAILED, note=str(error))
                self.say("Сверка не принята", f"{path.name}: {error}")
                move_aside(path, self.settings.folder("errors"))
                continue
            self.store.remember(
                digest,
                name=path.name,
                source=str(path),
                ticket=str(answer.get("ticket") or ""),
                status=state_module.SENT,
                note="",
            )
            sent += 1
            self.say("Сверка отправлена", f"{path.name}: идёт разбор на сервере.")
        return sent

    # --- шаг 2: ждём разбора ---

    def follow(self) -> int:
        ready = 0
        for row in self.store.waiting():
            ticket = str(row.get("ticket") or "")
            if not ticket:
                continue
            try:
                view = self.client.job(ticket)
            except Gone as error:
                self.store.remember(row["digest"], status=state_module.FAILED, note=str(error))
                self.say("Работа потеряна", f"{row.get('name')}: {error}")
                continue
            except ServerError as error:
                logger.warning("Состояние не прочитано: %s", error)
                continue
            if view.get("error"):
                self.store.remember(row["digest"], status=state_module.FAILED, note=str(view["error"]))
                self.say("Разбор не удался", f"{row.get('name')}: {view['error']}")
                move_aside(Path(str(row.get("source") or "")), self.settings.folder("errors"))
                continue
            if not view.get("ready"):
                continue
            token = str(view.get("token") or "")
            self.store.remember(row["digest"], token=token, status=state_module.READY)
            ready += 1
            address = f"{str(self.settings.server_url or '').rstrip('/')}/result/{token}"
            self.say(
                "Сверка разобрана",
                f"{row.get('name')}: проверьте спорные пары и нажмите «Выгрузить на компьютер».",
            )
            if self.settings.open_browser:
                try:
                    self._browser(address)
                except Exception:  # noqa: BLE001
                    logger.debug("Браузер не открылся", exc_info=True)
        return ready

    # --- шаг 3: выгрузка по кнопке человека ---

    def take_exports(self) -> int:
        folder = self.settings.folder("outbox")
        if folder is None:
            return 0
        try:
            queue = self.client.exports()
        except ServerError as error:
            logger.warning("Очередь выгрузок не прочитана: %s", error)
            return 0
        taken = 0
        for item in queue:
            token = str(item.get("token") or "")
            if not token:
                continue
            if self.save_export(token, item, folder):
                taken += 1
        return taken

    def save_export(self, token: str, item: dict, folder: Path) -> bool:
        """Забирает одну сверку: сам файл и архив снимков."""
        name = str(item.get("name") or f"Сверка-{token}.xlsx")
        target = free_path(folder, name)
        try:
            saved = self.client.fetch(token, RESULT_KIND, target)
        except ServerError as error:
            self.client_failed(token, str(error))
            self.say("Файл не забран", f"{name}: {error}")
            return False
        if saved is None:
            self.client_failed(token, "Сервер больше не держит эту сверку.")
            return False
        photos = None
        try:
            photos = self.client.fetch(token, PHOTOS_KIND, free_path(folder, f"{saved.stem} фото.zip"))
        except ServerError as error:
            logger.warning("Архив снимков не забран: %s", error)
        row = self.store.by_token(token)
        if row is not None:
            self.store.remember(row["digest"], status=state_module.SAVED, note=str(saved))
            move_aside(Path(str(row.get("source") or "")), self.settings.folder("archive"))
        try:
            self.client.done(token, f"Положено: {saved.name}")
        except ServerError as error:
            logger.warning("Сервер не отметил выгрузку: %s", error)
        tail = " и архив фото" if photos is not None else " (снимков нет)"
        self.say("Сверка выгружена", f"{saved.name}{tail}")
        return True

    def client_failed(self, token: str, note: str) -> None:
        try:
            self.client.failed(token, note)
        except ServerError:
            logger.debug("Сбой выгрузки не отмечен на сервере", exc_info=True)

    # --- проход целиком ---

    def tick(self) -> dict:
        troubles = self.settings.troubles()
        if troubles:
            return {"sent": 0, "ready": 0, "taken": 0, "troubles": troubles}
        return {
            "sent": self.send_new(),
            "ready": self.follow(),
            "taken": self.take_exports(),
            "troubles": [],
        }
