"""Копии справочников с сетевой папки и расписание обновления.

Файлы лежат в сетевой папке компании и постоянно меняются. Служба держит
местную копию и обновляет её:

- по расписанию (дни недели и время выбираются на странице настроек);
- по кнопке «Обновить справочники».

Расписание и пути хранятся в одном файле JSON, чтобы переживать перезапуск.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import refs

logger = logging.getLogger("excelkro.refs_sync")

WEEK_DAYS = (
    (0, "Пн"),
    (1, "Вт"),
    (2, "Ср"),
    (3, "Чт"),
    (4, "Пт"),
    (5, "Сб"),
    (6, "Вс"),
)

PLANNING_FILE = "planning.xlsx"
SCHEDULE_FILE = "schedule.xlsx"


@dataclass
class SyncState:
    """Состояние обновления справочников."""

    # Пути к исходным книгам в сетевой папке. Задаются один раз.
    planning_source: str = ""
    schedule_source: str = ""
    # Дни недели (0 — понедельник) и время вида "07:30".
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    times: list[str] = field(default_factory=lambda: ["07:30"])
    enabled: bool = True
    last_run: str = ""
    last_status: str = ""
    last_error: str = ""

    def slots(self) -> list[dt.time]:
        picked = []
        for text in self.times:
            moment = parse_time(text)
            if moment is not None:
                picked.append(moment)
        return sorted(set(picked))


def parse_time(value: object) -> dt.time | None:
    text = str(value or "").strip()
    try:
        hour, minute = text.split(":")
        return dt.time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def parse_days(values: list[object]) -> list[int]:
    days = []
    for value in values:
        try:
            day = int(str(value).strip())
        except ValueError:
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    return sorted(days)


def parse_times(values: list[object]) -> list[str]:
    times = []
    for value in values:
        moment = parse_time(value)
        if moment is not None:
            text = moment.strftime("%H:%M")
            if text not in times:
                times.append(text)
    return sorted(times)


def load_state(path: str | Path) -> SyncState:
    file = Path(path)
    if not file.is_file():
        return SyncState()
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Файл состояния справочников не читается: %s", file)
        return SyncState()
    known = {name: data[name] for name in SyncState().__dict__ if name in data}
    state = SyncState(**known)
    state.days = parse_days(list(state.days))
    state.times = parse_times(list(state.times))
    return state


def save_state(path: str | Path, state: SyncState) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def local_paths(local_dir: str | Path) -> tuple[Path, Path]:
    """Местные копии: планирование и график."""
    folder = Path(local_dir)
    return folder / PLANNING_FILE, folder / SCHEDULE_FILE


def _copy_one(source: str, target: Path) -> str:
    """Копирует одну книгу. Отдаёт текст ошибки или пустую строку."""
    if not source:
        return "путь не задан"
    origin = Path(source)
    if not origin.is_file():
        return f"файл не найден: {source}"
    target.parent.mkdir(parents=True, exist_ok=True)
    spare = target.with_suffix(target.suffix + ".new")
    try:
        shutil.copyfile(origin, spare)
        spare.replace(target)
    except OSError as error:
        spare.unlink(missing_ok=True)
        return f"не удалось скопировать: {error}"
    return ""


def refresh(state: SyncState, local_dir: str | Path, state_path: str | Path) -> SyncState:
    """Копирует обе книги и сбрасывает кеш разбора."""
    planning, schedule = local_paths(local_dir)
    troubles = []
    for source, target, title in (
        (state.planning_source, planning, "планирование"),
        (state.schedule_source, schedule, "график"),
    ):
        trouble = _copy_one(source, target)
        if trouble:
            troubles.append(f"{title}: {trouble}")

    state.last_run = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    state.last_error = "; ".join(troubles)
    state.last_status = "ошибка" if troubles else "готово"
    save_state(state_path, state)
    refs.forget_books()
    if troubles:
        logger.warning("Обновление справочников с ошибками: %s", state.last_error)
    else:
        logger.info("Справочники обновлены")
    return state


def due(state: SyncState, now: dt.datetime, last: dt.datetime | None) -> bool:
    """Наступило ли время очередной копии."""
    if not state.enabled or now.weekday() not in state.days:
        return False
    for slot in state.slots():
        moment = dt.datetime.combine(now.date(), slot)
        if moment <= now and (last is None or last < moment):
            return True
    return False


class Scheduler:
    """Отдельный поток: следит за расписанием копий."""

    def __init__(
        self,
        state_path: str | Path,
        local_dir: str | Path,
        tick_seconds: int = 30,
    ) -> None:
        self.state_path = Path(state_path)
        self.local_dir = Path(local_dir)
        self.tick_seconds = max(int(tick_seconds), 5)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dt.datetime | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="refs-sync", daemon=True)
        self._thread.start()
        logger.info("Планировщик обновления справочников запущен")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)

    def run_now(self) -> SyncState:
        state = refresh(load_state(self.state_path), self.local_dir, self.state_path)
        self._last = dt.datetime.now()
        return state

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                state = load_state(self.state_path)
                if due(state, dt.datetime.now(), self._last):
                    refresh(state, self.local_dir, self.state_path)
                    self._last = dt.datetime.now()
            except Exception:  # noqa: BLE001
                logger.exception("Сбой в планировщике справочников")
