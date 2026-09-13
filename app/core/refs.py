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

Разметка книг у людей плавает, поэтому разбор нарочно гибкий:

* в планировании строка недель и столбец магазинов ищутся по содержимому,
  а не берутся по номеру;
* недели в книге планирования идут подряд за несколько лет (в реальной
  книге это больше 250 столбцов), а год подписан в строке над шапкой не у
  каждого столбца, поэтому год читается по столбцу с продолжением от
  последнего известного;
* подписи недель бывают сокращёнными («Янв 31- Февр 6», «Авг 31 - Сент 06»),
  поэтому месяц определяется по началу слова;
* в графике фамилии ревизоров могут быть и внутри ячейки магазина через
  перевод строки, и в строках ниже даты — читаются оба варианта.

Подбор склада нечёткий, поэтому у каждого ответа есть уверенность:
`RefsInfo.confidence` — схожесть имени склада, `day_shift` — сдвиг даты в
графике. Неточные и неполные ответы помечаются `uncertain`: такие значения
в файл сразу не пишутся, их подтверждает человек на странице результата.

Важно о чтении книг: они открываются в режиме `read_only`, где обращение
`sheet.cell(row=..., column=...)` каждый раз заново разбирает весь XML листа.
Поэтому случайный доступ по ячейкам здесь запрещён: лист читается одним
проходом в матрицу значений (`_matrix`), а дальше работа идёт с ней.
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

# Страховка от гигантских листов: дальше этих пределов данных не бывает.
# Книга планирования ведётся годами подряд, столбцов в ней несколько сотен,
# поэтому запас по ширине большой: при меньшем пределе последние годы
# просто не читались и причина оставалась пустой.
MAX_ROWS = 20000
MAX_COLUMNS = 1000

# Сколько строк под датой в графике относятся к этой же дате.
BLOCK_ROWS = 8

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

# Полные названия месяцев и сокращения, которые встречаются в шапках недель.
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
    "янв": 1,
    "февр": 2,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сент": 9,
    "сен": 9,
    "окт": 10,
    "нояб": 11,
    "ноя": 11,
    "дек": 12,
}
# Сначала длинные ключи: «февраль» не должен определяться по «фев».
_MONTH_KEYS = sorted(MONTHS, key=len, reverse=True)

# «Январь 10-16», «Май 1 - 7», «Июнь 29-5», «Янв 31- Февр 6», «Сент 25-Окт 1»
WEEK = re.compile(
    r"([А-Яа-яЁё]+)\.?\s*(\d{1,2})\s*[-–]\s*(\d{1,2})",
)
# Полное ФИО: три слова с большой буквы.
FULL_NAME = re.compile(
    r"^([А-ЯЁ][а-яё\-]+)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)$",
)
# «Павлова», «Павлова М.», «Павлова М. А.», «Павлова М.А.»
SHORT_NAME = re.compile(
    r"^([А-ЯЁ][а-яё\-]+)(?:\s*([А-ЯЁ])\.?(?:\s*([А-ЯЁ])\.?)?)?$",
)

NOT_A_NAME = (
    "дата",
    "открытия",
    "закрытия",
    "итого",
    "склад",
    "магазин",
    "отпуск",
    "выходной",
    "больничный",
)

# Порог, ниже которого подбор склада требует подтверждения человеком.
CONFIRM_MIN_SCORE = 0.95


@dataclass
class RefsInfo:
    """Данные для одной инвентаризации. Пустая строка — значение не найдено."""

    reason: str = ""
    admin: str = ""
    checker: str = ""
    auditors: tuple[str, ...] = ()
    # Схожесть имени склада с именем из книги: 1.0 — точное совпадение.
    confidence: float = 1.0
    # Сдвиг найденной записи графика относительно даты сверки, дни.
    day_shift: int = 0
    # Понятные причины сомнения: показываются на странице результата.
    notes: tuple[str, ...] = ()
    # Данные неточные или неполные: нужно подтверждение человека.
    uncertain: bool = False

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


def _month_number(word: object) -> int:
    """Номер месяца по названию, в том числе сокращённому.

    В шапках недель месяц пишут как угодно: «Сентябрь», «Сент», «Сен».
    Поэтому сравнение идёт по началу слова, от длинных названий к коротким.
    """
    text = _clean(word).lower().replace("ё", "е")
    if text in MONTHS:
        return MONTHS[text]
    for key in _MONTH_KEYS:
        if text.startswith(key):
            return MONTHS[key]
    return 0


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


# --- Чтение листа одним проходом --------------------------------------------


def _matrix(sheet, max_rows: int = MAX_ROWS, max_columns: int = MAX_COLUMNS) -> list[list]:
    """Значения листа списком строк. Один проход по файлу.

    В режиме `read_only` случайный доступ `sheet.cell(...)` разбирает весь
    лист заново на каждое обращение, поэтому книга на несколько тысяч строк
    читалась бы часами. Здесь лист читается ровно один раз.
    """
    rows: list[list] = []
    clipped = False
    for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
        clipped = clipped or len(values) > max_columns
        rows.append(list(values[:max_columns]))
        if number >= max_rows:
            logger.warning(
                "Лист «%s» обрезан на %s строках",
                getattr(sheet, "title", "?"),
                max_rows,
            )
            break
    if clipped:
        logger.warning(
            "Лист «%s» обрезан на %s столбцах: часть недель не прочитана",
            getattr(sheet, "title", "?"),
            max_columns,
        )
    return rows


def _width(rows: list[list]) -> int:
    """Число столбцов в прочитанной матрице."""
    return max((len(line) for line in rows), default=0)


def _at(rows: list[list], row: int, column: int) -> object:
    """Значение ячейки матрицы. Нумерация с единицы, как в Excel."""
    if 1 <= row <= len(rows):
        line = rows[row - 1]
        if 1 <= column <= len(line):
            return line[column - 1]
    return None


def _lines(rows: list[list], row: int, column: int) -> list[str]:
    """Непустые строки внутри одной ячейки."""
    parts = str(_at(rows, row, column) or "").split("\n")
    return [text for text in (_clean(part) for part in parts) if text]


# --- Книга планирования -----------------------------------------------------


def _week_row(rows: list[list], limit: int = 12) -> int:
    """Строка с подписями недель. Ищется по содержимому, а не по номеру."""
    best_row, best_count = 0, 0
    for row in range(1, min(len(rows), limit) + 1):
        count = sum(
            1
            for column in range(1, _width(rows) + 1)
            if WEEK.search(_clean(_at(rows, row, column)))
        )
        if count > best_count:
            best_row, best_count = row, count
    return best_row if best_count >= 2 else 0


def _year_at(rows: list[list], header_row: int, column: int) -> int:
    """Год, подписанный над шапкой в этом столбце. 0 — подписи нет."""
    for row in range(1, max(header_row, 1)):
        value = _at(rows, row, column)
        if isinstance(value, (int, float)) and 2000 < int(value) < 2100:
            return int(value)
        match = re.search(r"20\d{2}", _clean(value))
        if match:
            return int(match.group(0))
    return 0


def _sheet_year(rows: list[list], header_row: int, default: int) -> int:
    """Год первых столбцов листа: самая левая подпись года над шапкой.

    Подпись года стоит не в начале листа, а над первым столбцом недель, и у
    разных управляющих этот столбец разный, поэтому ищем по всей ширине.
    """
    for column in range(1, _width(rows) + 1):
        year = _year_at(rows, header_row, column)
        if year:
            return year
    return default


def _week_columns(
    rows: list[list], year: int, header_row: int
) -> dict[int, tuple[dt.date, dt.date]]:
    """Столбцы недель: номер столбца -> (первый день, последний день).

    Год берётся из подписи над столбцом, если она есть. Там, где подписи
    нет, год продолжается от предыдущего столбца и увеличивается на переходе
    через январь: недели в книге идут подряд по календарю.
    """
    spans: dict[int, tuple[dt.date, dt.date]] = {}
    current_year = year
    last_month = 0
    for column in range(1, _width(rows) + 1):
        label = _clean(_at(rows, header_row, column))
        match = WEEK.search(label)
        if not match:
            continue
        month = _month_number(match.group(1))
        if not month:
            continue
        marked = _year_at(rows, header_row, column)
        if marked:
            if marked != current_year:
                last_month = 0
            current_year = marked
        elif month < last_month:
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


def _store_column(
    rows: list[list],
    header_row: int,
    spans: dict[int, tuple[dt.date, dt.date]],
    limit: int = 8,
) -> int:
    """Столбец с названиями магазинов: самый «текстовый» из первых столбцов."""
    best_column, best_count = 0, 0
    for column in range(1, min(_width(rows), limit) + 1):
        if column in spans:
            continue
        count = 0
        for row in range(header_row + 1, len(rows) + 1):
            text = _clean(_at(rows, row, column))
            if len(text) < 3 or WEEK.search(text):
                continue
            if text.replace(",", ".").replace(".", "").isdigit():
                continue
            count += 1
        if count > best_count:
            best_column, best_count = column, count
    return best_column or 2


def read_planning(path: str | Path) -> dict[str, list[tuple[dt.date, dt.date, str, str]]]:
    """Читает книгу планирования: причина по складу и неделе."""
    plans: dict[str, list[tuple[dt.date, dt.date, str, str]]] = {}
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for name in book.sheetnames:
            low = name.strip().lower()
            if any(low.startswith(skip) for skip in PLANNING_SKIP):
                continue
            rows = _matrix(book[name])
            admin = _clean(name)
            header_row = _week_row(rows)
            if not header_row:
                logger.warning("Лист планирования «%s»: строка недель не найдена", name)
                continue
            year = _sheet_year(rows, header_row, dt.date.today().year)
            spans = _week_columns(rows, year, header_row)
            if not spans:
                continue
            store_column = _store_column(rows, header_row, spans)
            for row in range(header_row + 1, len(rows) + 1):
                store = normalize_store(_at(rows, row, store_column))
                if not store:
                    continue
                bucket = plans.setdefault(store, [])
                for column, (start, end) in spans.items():
                    reason = _clean(_at(rows, row, column))
                    if not reason:
                        continue
                    reason = reason.replace("\n", " / ")
                    bucket.append((start, end, reason, admin))
    finally:
        book.close()
    return plans


# --- Книга графика ----------------------------------------------------------


def _admin_columns(rows: list[list], header_row: int) -> dict[int, str]:
    columns: dict[int, str] = {}
    for column in range(2, _width(rows) + 1):
        name = _clean(_at(rows, header_row, column))
        if name and _looks_like_name(name):
            columns[column] = name
    return columns


def _block_end(rows: list[list], row: int) -> int:
    """Последняя строка, относящаяся к дате из строки `row`.

    Фамилии ревизоров в графике часто стоят не внутри ячейки магазина, а в
    строках под датой, у которых столбец с датой пустой.
    """
    last = row
    while last < len(rows) and last - row < BLOCK_ROWS:
        following = last + 1
        values = rows[following - 1]
        if _as_date(values[0] if values else None) is not None:
            break
        if len(_admin_columns(rows, following)) >= 3:
            break
        last = following
    return last


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

        rows = _matrix(book[SCHEDULE_SHEET])
        admins: dict[int, str] = {}
        row = 1
        while row <= len(rows):
            values = rows[row - 1]
            day = _as_date(values[0] if values else None)
            if day is None:
                # Шапка повторяется по всему листу: состав столбцов меняется.
                fresh = _admin_columns(rows, row)
                if len(fresh) >= 3:
                    admins = fresh
                row += 1
                continue
            if not admins:
                row += 1
                continue
            last = _block_end(rows, row)
            for column, admin in admins.items():
                lines: list[str] = []
                for line_row in range(row, last + 1):
                    lines.extend(_lines(rows, line_row, column))
                if not lines:
                    continue
                store = normalize_store(lines[0])
                people = tuple(part for part in lines[1:] if _looks_like_name(part))
                if not store:
                    continue
                visits[(store, day)] = (admin, people)
            row = last + 1
    finally:
        book.close()
    return visits, by_surname


def _collect_names(sheet, by_surname: dict[str, list[str]]) -> None:
    for row in sheet.iter_rows(values_only=True):
        for value in row:
            for text in (_clean(part) for part in str(value or "").split("\n")):
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


def _match_store(store: str, known: list[str], min_score: float) -> tuple[str, float]:
    """Ближайшее имя склада и его схожесть. Пустое имя — совпадений нет."""
    if store in known:
        return store, 1.0
    best, score = "", 0.0
    for name in known:
        ratio = similarity(store, name)
        if ratio > score:
            best, score = name, ratio
    if score < min_score:
        return "", score
    return best, score


def _pick_plan(
    entries: list[tuple[dt.date, dt.date, str, str]],
    day: dt.date,
    days_around: int,
) -> tuple[str, str, int] | None:
    """Причина по неделе, в которую попадает дата. Иначе ближайшая неделя."""
    for start, end, text, admin in entries:
        if start <= day <= end:
            return text, admin, 0
    picked: tuple[str, str, int] | None = None
    for start, end, text, admin in entries:
        gap = min(abs((day - start).days), abs((day - end).days))
        if gap <= days_around and (picked is None or gap < picked[2]):
            picked = (text, admin, gap)
    return picked


def lookup(
    warehouse: str,
    day: dt.date | None,
    books: RefsBooks,
    checker: str = "",
    min_score: float = 0.80,
    days_around: int = 3,
    confirm_min_score: float = CONFIRM_MIN_SCORE,
) -> RefsInfo:
    """Собирает данные по складу и дате инвентаризации.

    Порог `min_score` — граница, ниже которой склад считается не найденным.
    Всё, что найдено по неточному имени, со сдвигом даты или найдено не
    полностью, помечается `uncertain` и требует подтверждения человеком.
    """
    store = normalize_store(warehouse)
    if not store or day is None:
        return RefsInfo(checker=checker, confidence=0.0)

    notes: list[str] = []
    scores: list[float] = []
    shift = 0
    plan_gap = 0

    admin, people = "", ()
    visit_stores = sorted({name for name, _ in books.visits})
    visit_key, visit_score = _match_store(store, visit_stores, min_score)
    if visit_key:
        for step_size in range(0, days_around + 1):
            for step in ((0,) if step_size == 0 else (-step_size, step_size)):
                found = books.visits.get((visit_key, day + dt.timedelta(days=step)))
                if found:
                    admin, people = found
                    shift = step
                    break
            if admin or people:
                break
        if admin or people:
            scores.append(visit_score)
            if visit_score < 1.0:
                notes.append(
                    f"Склад «{warehouse}» сопоставлен с «{visit_key}» в графике, "
                    f"схожесть {visit_score:.2f}."
                )
            if shift:
                notes.append(
                    f"Запись в графике взята со сдвигом {shift:+d} дн. "
                    f"от даты сверки {day:%d.%m.%Y}."
                )
            if not people:
                notes.append(
                    "Ревизоры в графике не распознаны: в ячейке распределения "
                    "фамилий нет. Впишите их вручную."
                )

    reason = ""
    plan_key, plan_score = _match_store(store, sorted(books.plans), min_score)
    if plan_key:
        picked = _pick_plan(books.plans[plan_key], day, days_around)
        if picked:
            reason, plan_admin, plan_gap = picked
            admin = admin or plan_admin
            scores.append(plan_score)
            if plan_score < 1.0:
                notes.append(
                    f"Склад «{warehouse}» сопоставлен с «{plan_key}» в планировании, "
                    f"схожесть {plan_score:.2f}."
                )
            if plan_gap:
                notes.append(
                    f"Причина взята из недели, отстоящей от даты сверки "
                    f"на {plan_gap} дн."
                )
    if not reason:
        notes.append(
            "Причина инвентаризации в планировании не найдена: "
            "укажите её вручную."
        )

    auditors = tuple(full_name(person, books.by_surname) for person in people)
    info = RefsInfo(
        reason=reason,
        admin=admin,
        checker=checker,
        auditors=auditors,
        confidence=min(scores) if scores else 0.0,
        day_shift=shift,
        notes=tuple(notes),
    )
    info.uncertain = bool(
        info.found
        and (
            info.confidence < confirm_min_score
            or shift != 0
            or plan_gap != 0
            or not reason
            or not auditors
        )
    )
    if not info.uncertain:
        info.notes = ()
    return info


def pending(info: RefsInfo) -> RefsInfo:
    """Версия ответа без неподтверждённых значений.

    Проверяющий всегда один и тот же, поэтому он остаётся. Остальное ждёт
    подтверждения человека и в файл сверки пока не пишется.
    """
    return RefsInfo(
        checker=info.checker,
        confidence=info.confidence,
        day_shift=info.day_shift,
        notes=info.notes,
        uncertain=info.uncertain,
    )


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
