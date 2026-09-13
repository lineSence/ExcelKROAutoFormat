"""Местные копии справочников и их состояние.

Главный способ: ручная загрузка двух книг Excel из браузера на странице
`/refs`. Файлы ложатся в папку службы и живут там до следующей загрузки.

Число складов и фамилий считается один раз после загрузки и хранится в состоянии:
разбор больших книг тяжёлый, и делать его на каждый показ страницы нельзя.

Здесь же хранится итог последней обработки сверки: если данные справочников
не подставились, причины видны на странице «Справочники» без журнала службы.

Запасной способ (пока не включён в интерфейсе): копирование из сетевой
папки по расписанию. Код сохранён для будущего.
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

# Сколько последних обработок со сбоями справочников хранить для страницы.
FILL_LOG_LIMIT = 10

# Виды книг: ключ формы — имя местной копии — название для страницы.
BOOK_KINDS = {
    "planning": (PLANNING_FILE, "книга планирования"),
    "schedule": (SCHEDULE_FILE, "книга графика"),
}


@dataclass
class SyncState:
    """Состояние справочников."""

    # Имена файлов, как их загрузил человек, и время загрузки.
    planning_name: str = ""
    planning_loaded: str = ""
    schedule_name: str = ""
    schedule_loaded: str = ""
    # Итог разбора: число складов, число фамилий с полным ФИО и его итог.
    stores: int = 0
    people: int = 0
    parsed: str = ""
    # Пути к книгам в сетевой папке. Пока не используются.
    planning_source: str = ""
    schedule_source: str = ""
    # Дни недели (0 — понедельник) и время вида "07:30".
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    times: list[str] = field(default_factory=lambda: ["07:30"])
    # Копирование по расписанию по умолчанию выключено.
    enabled: bool = False
    last_run: str = ""
    last_status: str = ""
    last_error: str = ""
    # Журнал последних обработок сверки для блока ошибок на странице.
    # Каждая запись: {"at", "store", "day", "file", "found", "problems"}.
    fill_log: list[dict] = field(default_factory=list)

    def slots(self) -> list[dt.time]:
        picked = []
        for text in self.times:
            moment = parse_time(text)
            if moment is not None:
                picked.append(moment)
        return sorted(set(picked))

    @property
    def fill_last(self) -> dict | None:
        """Последняя обработка сверки или None, если их ещё не было."""
        return self.fill_log[0] if self.fill_log else None

    @property
    def fill_troubles(self) -> list[dict]:
        """Только те обработки, где были ошибки справочников."""
        return [item for item in self.fill_log if item.get("problems")]


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


def parse_fill_log(values: object) -> list[dict]:
    """Чистит журнал обработок из файла состояния."""
    if not isinstance(values, list):
        return []
    clean: list[dict] = []
    for item in values[:FILL_LOG_LIMIT]:
        if not isinstance(item, dict):
            continue
        problems = item.get("problems")
        clean.append(
            {
                "at": str(item.get("at") or ""),
                "store": str(item.get("store") or ""),
                "day": str(item.get("day") or ""),
                "file": str(item.get("file") or ""),
                "found": bool(item.get("found")),
                "problems": [str(line) for line in problems] if isinstance(problems, list) else [],
            }
        )
    return clean


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
    state.fill_log = parse_fill_log(state.fill_log)
    return state


def save_state(path: str | Path, state: SyncState) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def note_fill(
    state_path: str | Path,
    store: str,
    day: str,
    file_name: str,
    found: bool,
    problems: list[str],
) -> SyncState:
    """Записывает итог подстановки справочников в одну сверку.

    Журнал нужен, чтобы причина была видна на странице «Справочники» позже,
    а не только сразу после обработки.
    """
    state = load_state(state_path)
    entry = {
        "at": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "store": str(store or ""),
        "day": str(day or ""),
        "file": str(file_name or ""),
        "found": bool(found),
        "problems": [str(line) for line in problems],
    }
    state.fill_log = [entry] + list(state.fill_log)[: FILL_LOG_LIMIT - 1]
    save_state(state_path, state)
    return state


def clear_fill_log(state_path: str | Path) -> SyncState:
    """Очищает блок ошибок справочников."""
    state = load_state(state_path)
    state.fill_log = []
    save_state(state_path, state)
    return state


def local_paths(local_dir: str | Path) -> tuple[Path, Path]:
    """Местные копии: планирование и график."""
    folder = Path(local_dir)
    return folder / PLANNING_FILE, folder / SCHEDULE_FILE


def book_target(local_dir: str | Path, kind: str) -> Path:
    """Куда ложится загруженная книга выбранного вида."""
    if kind not in BOOK_KINDS:
        raise ValueError(f"неизвестный вид книги: {kind}")
    name, _ = BOOK_KINDS[kind]
    return Path(local_dir) / name


def mark_upload(
    state: SyncState,
    kind: str,
    file_name: str,
    state_path: str | Path,
) -> SyncState:
    """Запоминает имя и время ручной загрузки книги."""
    stamp = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    if kind == "planning":
        state.planning_name = file_name
        state.planning_loaded = stamp
    elif kind == "schedule":
        state.schedule_name = file_name
        state.schedule_loaded = stamp
    else:
        raise ValueError(f"неизвестный вид книги: {kind}")
    state.last_run = stamp
    state.last_status = "загружено вручную"
    state.last_error = ""
    # Старый итог разбора больше не верен.
    state.stores = 0
    state.people = 0
    state.parsed = ""
    save_state(state_path, state)
    refs.forget_books()
    return state


def measure(state: SyncState, local_dir: str | Path, state_path: str | Path) -> SyncState:
    """Разбирает загруженные книги один раз и запоминает итог."""
    planning, schedule = local_paths(local_dir)
    try:
        books = refs.load_books(str(planning), str(schedule), force=True)
    except Exception as error:  # noqa: BLE001
        logger.exception("Не удалось разобрать справочники")
        state.stores = 0
        state.people = 0
        state.parsed = ""
        state.last_status = "ошибка"
        state.last_error = f"разбор не выполнен: {type(error).__name__}: {error}"
        save_state(state_path, state)
        return state
    state.stores = books.stores
    state.people = len(books.by_surname)
    state.parsed = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    state.last_status = "разобрано"
    state.last_error = ""
    save_state(state_path, state)
    return state


def book_status(local_dir: str | Path) -> dict[str, dict]:
    """Есть ли местные копии и каков их размер."""
    report: dict[str, dict] = {}
    for kind, (name, title) in BOOK_KINDS.items():
        file = Path(local_dir) / name
        found = file.is_file()
        report[kind] = {
            "title": title,
            "found": found,
            "size_mb": round(file.stat().st_size / 1048576, 2) if found else 0.0,
        }
    return report


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
    """Копирует обе книги из сетевой папки и сбрасывает кеш разбора."""
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
    state.stores = 0
    state.people = 0
    state.parsed = ""
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
    """Отдельный поток: следит за расписанием копий. Пока не запускается."""

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
                logger.exception("Сбой в планировщике справочников"}
