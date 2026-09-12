"""Справочники: причина инвентаризации, администратор и ревизоры.

Два источника (книги Excel на сетевой папке компании):

1. Книга планирования («НОВ ПЛАНИРОВАНИЕ»): лист на каждого управляющего
   группой магазинов (администратора). В строках — магазины, в столбцах —
   недели года, в пересечении — причина инвентаризации.
2. Книга графика («График Ревизий»), лист «Переучеты»: строки — даты,
   столбцы — администраторы, в ячейке — магазин и под ним фамилии ревизоров.

Ревизоры берутся из книги графика (распределение) — это главный источник.
Причина берётся из книги планирования. Если пару «склад + дата» найти не
удалось, поля остаются пустыми. Никакие служебные пометки в файл сверки
не пишутся.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl

logger = logging.getLogger("excelkro.refs")

SCHEDULE_SHEET = "Переучеты"

# Листы книги планирования, которые не относятся к администраторам.
PLANNING_SKIP = (
    "обозначения",
    "закр маг",
    "закрытые магазины",
    "лист",
    "свод",
    "итог",
)

# Листы книги графика, где лежат полные ФИО и телефоны.
NAME_SHEETS = ("тлф", "тлф помощников", "график помощников", "подотчетники")

MONTHS = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}

# «Январь 10-16», «Май 1 - 7», «Июнь 29-5»
WEEK = re.compile(
    r"([А-Яа-яЁё]+)\s*(\d{1,2})\s*[-–]\s*(\d{1,2})",
)
# Полное ФИО: три слова с большой буквы.
FULL_NAME = re.compile(
    r"^([А-ЯЁ][а-яё\-]+)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)$",
)
# «Павлова М.», «Смирнова»
SHORT_NAME = re.compile(r"^([А-ЯЁ][а-яё\-]+)\s*([А-ЯЁ])?\.?$")

NOT_A_NAME = (
    "дата",
    "открытия",
    "закрытия",
    "итого",
    "склад",
    "отпуск",
    "выходной",
)


@dataclass
class RefsInfo:
    """Данные для одной инвентаризации. Пустая строка — значение не найдено."""

    reason: str = ""
    admin: str = ""
    checker: str = ""
    auditors: tuple[str, ...] = ()

    @property
    def auditors_text(self) -> str:
        return ", ".join(self.auditors)

    @property
    def found(self) -> bool:
        return bool(self.reason or self.admin or self.auditors)


@dataclass
class RefsBooks:
    """Разобранное содержимое справочников."""

    # (склад) -> [(дата начала, дата конца, причина, администратор)]
    plans: dict[str, list[tuple[dt.date, dt.date, str, str]]] = field(default_factory=dict)
    # (склад, дата) -> (администратор, фамилии ревизоров)
    visits: dict[tuple[str, dt.date], tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    # фамилия -> список полных ФИО
    by_surname: dict[str, list[str]] = field(default_factory=dict)
    stamp: tuple[float, float] = (0.0, 0.0)

    @property
    def stores(self) -> int:
        names = {store for store in self.plans}
        names.update(store for store, _ in self.visits)
        return len(names)


def normalize_store(value: object) -> str:
    """Имя склада без уточнений в скобках, регистра и знаков."""
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^0-9a-zа-я]+", "", text)
    return text


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _surname_key(value: str) -> str:
    return value.lower().replace("ё", "е")


def similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def _as_date(value: object) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _clean(value)
    for shape in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, shape).date()
        except ValueError:
            continue
    return None


def _looks_like_name(text: str) -> bool:
    if not text or any(word in text.lower() for word in NOT_A_NAME):
        return False
    return bool(SHORT_NAME.match(text) or FULL_NAME.match(text))


# --- Книга планирования -----------------------------------------------------


def _week_columns(sheet, year: int) -> dict[int, tuple[dt.date, dt.date]]:
    """Столбцы недель: номер столбца -> (первый день, последний день)."""
    spans: dict[int, tuple[dt.date, dt.date]] = {}
    current_year = year
    last_month = 0
    for column in range(1, sheet.max_column + 1):
        label = _clean(sheet.cell(row=2, column=column).value)
        match = WEEK.search(label)
        if not match:
            continue
        month = MONTHS.get(match.group(1).strip().lower().replace("ё", "е"))
        if not month:
            continue
        if month < last_month:
            # Новый год начался: столбцы идут подряд по календарю.
            current_year += 1
        last_month = month
        first_day, last_day = int(match.group(2)), int(match.group(3))
        try:
            start = dt.date(current_year, month, first_day)
        except ValueError:
            continue
        end = start + dt.timedelta(days=6)
        if last_day >= first_day:
            try:
                end = dt.date(current_year, month, last_day)
            except ValueError:
                pass
        spans[column] = (start, end)
    return spans


def _sheet_year(sheet, default: int) -> int:
    for column in range(1, min(sheet.max_column, 20) + 1):
        value = sheet.cell(row=1, column=column).value
        if isinstance(value, (int, float)) and 2000 < int(value) < 2100:
            return int(value)
        match = re.search(r"20\d{2}", _clean(value))
        if match:
            return int(match.group(0))
    return default


def read_planning(path: str | Path) -> dict[str, list[tuple[dt.date, dt.date, str, str]]]:
    """Читает книгу планирования: причина по складу и неделе."""
    plans: dict[str, list[tuple[dt.date, dt.date, str, str]]] = {}
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in book.sheetnames:
            low = name.strip().lower()
            if any(low.startswith(skip) for skip in PLANNING_SKIP):
                continue
            sheet = book[name]
            admin = _clean(name)
            year = _sheet_year(sheet, dt.date.today().year)
            spans = _week_columns(sheet, year)
            if not spans:
                continue
            for row in range(3, sheet.max_row + 1):
                store = normalize_store(sheet.cell(row=row, column=2).value)
                if not store:
                    continue
                bucket = plans.setdefault(store, [])
                for column, (start, end) in spans.items():
                    reason = _clean(sheet.cell(row=row, column=column).value)
                    if not reason:
                        continue
                    reason = reason.replace("\n", " / ")
                    bucket.append((start, end, reason, admin))
    finally:
        book.close()
    return plans


# --- Книга графика ----------------------------------------------------------


def _admin_columns(sheet, header_row: int) -> dict[int, str]:
    columns: dict[int, str] = {}
    for column in range(2, sheet.max_column + 1):
        name = _clean(sheet.cell(row=header_row, column=column).value)
        if name and _looks_like_name(name):
            columns[column] = name
    return columns


def read_schedule(path: str | Path) -> tuple[
    dict[tuple[str, dt.date], tuple[str, tuple[str, ...]]],
    dict[str, list[str]],
]:
    """Читает книгу графика: распределение по датам и список полных ФИО."""
    visits: dict[tuple[str, dt.date], tuple[str, tuple[str, ...]]] = {}
    by_surname: dict[str, list[str]] = {}

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in book.sheetnames:
            if name.strip().lower() in NAME_SHEETS:
                _collect_names(book[name], by_surname)

        if SCHEDULE_SHEET not in book.sheetnames:
            logger.warning("В книге графика нет листа %s", SCHEDULE_SHEET)
            return visits, by_surname

        sheet = book[SCHEDULE_SHEET]
        admins: dict[int, str] = {}
        for row in range(1, sheet.max_row + 1):
            values = [sheet.cell(row=row, column=col).value for col in range(1, sheet.max_column + 1)]
            day = _as_date(values[0] if values else None)
            if day is None:
                # Шапка повторяется по всему листу: состав столбцов меняется.
                fresh = _admin_columns(sheet, row)
                if len(fresh) >= 3:
                    admins = fresh
                continue
            if not admins:
                continue
            for column, admin in admins.items():
                cell = values[column - 1] if column - 1 < len(values) else None
                lines = [_clean(part) for part in str(cell or "").split("\n")]
                lines = [part for part in lines if part]
                if not lines:
                    continue
                store = normalize_store(lines[0])
                people = tuple(part for part in lines[1:] if _looks_like_name(part))
                if not store:
                    continue
                visits[(store, day)] = (admin, people)
    finally:
        book.close()
    return visits, by_surname


def _collect_names(sheet, by_surname: dict[str, list[str]]) -> None:
    for row in sheet.iter_rows(values_only=True):
        for value in row:
            text = _clean(value)
            match = FULL_NAME.match(text)
            if not match:
                continue
            key = _surname_key(match.group(1))
            names = by_surname.setdefault(key, [])
            if text not in names:
                names.append(text)


# --- Разворот фамилии в полное ФИО ------------------------------------------


def full_name(short: str, by_surname: dict[str, list[str]]) -> str:
    """Разворачивает «Павлова М.» в полное ФИО. Иначе отдаёт как есть."""
    text = _clean(short)
    if FULL_NAME.match(text):
        return text
    match = SHORT_NAME.match(text)
    if not match:
        return text
    surname, initial = match.group(1), match.group(2)
    names = by_surname.get(_surname_key(surname))
    if not names:
        # Опечатка в графике: ищем ближайшую фамилию.
        best, score = "", 0.0
        for key in by_surname:
            ratio = similarity(_surname_key(surname), key)
            if ratio > score:
                best, score = key, ratio
        if score < 0.86:
            return text
        names = by_surname[best]
    if initial:
        picked = [name for name in names if name.split()[1].startswith(initial)]
        if len(picked) == 1:
            return picked[0]
        return text
    if len(names) == 1:
        return names[0]
    # Однофамильцы без инициала: однозначно определить нельзя.
    return text


# --- Чтение с кешем ---------------------------------------------------------

_LOCK = threading.Lock()
_CACHE: RefsBooks | None = None


def _stamp(planning: Path, schedule: Path) -> tuple[float, float]:
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return (mtime(planning), mtime(schedule))


def load_books(planning_path: str, schedule_path: str, force: bool = False) -> RefsBooks:
    """Читает справочники. Повторное чтение только при смене файлов."""
    global _CACHE
    planning, schedule = Path(planning_path or ""), Path(schedule_path or "")
    stamp = _stamp(planning, schedule)
    with _LOCK:
        if not force and _CACHE is not None and _CACHE.stamp == stamp:
            return _CACHE
        books = RefsBooks(stamp=stamp)
        if planning.is_file():
            try:
                books.plans = read_planning(planning)
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось прочитать книгу планирования")
        if schedule.is_file():
            try:
                books.visits, books.by_surname = read_schedule(schedule)
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось прочитать книгу графика")
        _CACHE = books
        return books


def forget_books() -> None:
    """Сбрасывает кеш: вызывается кнопкой «Обновить справочники»."""
    global _CACHE
    with _LOCK:
        _CACHE = None


# --- Поиск данных по складу и дате ------------------------------------------


def _match_store(store: str, known: list[str], min_score: float) -> str:
    if store in known:
        return store
    best, score = "", 0.0
    for name in known:
        ratio = similarity(store, name)
        if ratio > score:
            best, score = name, ratio
    return best if score >= min_score else ""


def lookup(
    warehouse: str,
    day: dt.date | None,
    books: RefsBooks,
    checker: str = "",
    min_score: float = 0.90,
    days_around: int = 3,
) -> RefsInfo:
    """Собирает данные по складу и дате инвентаризации."""
    store = normalize_store(warehouse)
    if not store or day is None:
        return RefsInfo(checker=checker)

    admin, people = "", ()
    visit_stores = sorted({name for name, _ in books.visits})
    visit_key = _match_store(store, visit_stores, min_score)
    if visit_key:
        for shift in range(0, days_around + 1):
            for step in ((0,) if shift == 0 else (-shift, shift)):
                found = books.visits.get((visit_key, day + dt.timedelta(days=step)))
                if found:
                    admin, people = found
                    break
            if admin or people:
                break

    reason = ""
    plan_key = _match_store(store, sorted(books.plans), min_score)
    if plan_key:
        for start, end, text, plan_admin in books.plans[plan_key]:
            if start <= day <= end:
                reason = text
                admin = admin or plan_admin
                break

    auditors = tuple(full_name(person, books.by_surname) for person in people)
    return RefsInfo(reason=reason, admin=admin, checker=checker, auditors=auditors)


def write_cells(sheet, info: RefsInfo, cells: dict[str, str]) -> list[str]:
    """Пишет найденные значения в ячейки готового файла.

    Пустые значения не пишутся: служебных пометок в сверке быть не должно.
    """
    written: list[str] = []
    values = {
        "reason": info.reason,
        "admin": info.admin,
        "checker": info.checker,
        "auditors": info.auditors_text,
    }
    for field_name, address in cells.items():
        value = values.get(field_name, "")
        if not address or not value:
            continue
        try:
            sheet[address] = value
        except (KeyError, ValueError):
            logger.warning("Неверный адрес ячейки в настройках: %s", address)
            continue
        written.append(address)
    return written
