"""Шаг 2. Разбор структуры файла 1С."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

TITLE_PREFIX = "Инвентаризация товаров"
TITLE_MARKS = (
    "инвентаризация товаров",
    "инвентаризационная опись",
    "сличительная ведомость",
    "инвентаризация",
)
SCAN_ROWS = 80
SCAN_COLUMNS = 12
HEADER_MARK_A = "№"
HEADER_MARK_C = "Хар-ка"
COL_CODE = 1       # A
COL_NAME = 2       # B
COL_TRAIT = 3      # C
COL_FACT = 4       # D
COL_BOOK = 5       # E
COL_DIFF = 6       # F
COL_PRICE = 7      # G
COL_SUM_DIFF = 10  # J
COL_DOC = 11       # K

DATE_PATTERN = re.compile(r"(\d{2}\.\d{2}\.\d{4})")
NUMBER_PATTERN = re.compile(r"№\s*([\w\-]+)")
MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11,
    "декабр": 12,
}
WORD_DATE_PATTERN = re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})")


class ParseError(Exception):
    """Файл не похож на сверку из 1С."""


@dataclass
class Group:
    """Блок одной группы товаров."""

    name: str
    header_row: int
    first_data_row: int
    last_data_row: int
    total_row: int

    @property
    def data_rows(self) -> range:
        return range(self.first_data_row, self.last_data_row + 1)


@dataclass
class Document:
    """Разобранный файл сверки."""

    title_row: int | None
    title_text: str
    doc_number: str | None
    doc_date: str | None
    warehouse_row: int | None
    organization_row: int | None
    groups: list[Group] = field(default_factory=list)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%d.%m.%Y")
    return str(value).strip()


def _fold(text: str) -> str:
    """Убирает неразрывные пробелы, лишние пробелы и регистр."""
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def head_sample(sheet, rows: int = 12) -> list[str]:
    """Первые непустые тексты листа. Нужны для разбора ошибок."""
    sample: list[str] = []
    for row in range(1, min(sheet.max_row, rows) + 1):
        parts = []
        for column in range(1, min(sheet.max_column, SCAN_COLUMNS) + 1):
            text = _text(sheet.cell(row=row, column=column).value)
            if text:
                parts.append(f"{row}:{column}={text[:40]}")
        if parts:
            sample.append("; ".join(parts))
    return sample


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def warehouse_from_filename(filename: str) -> str:
    """Имя склада из имени входного файла (решение R11)."""
    stem = Path(filename).stem.strip()
    lowered = stem.lower()
    cut = lowered.find(" без ")
    if cut > 0:
        return stem[:cut].strip()
    return stem.split(" ")[0].strip()


def find_title(sheet) -> tuple[int | None, str]:
    """Ищет титул в первых строках и в первых столбцах.

    Титул в выгрузке 1С стоит не всегда в ячейке A1. Он бывает в другом
    столбце, с неразрывными пробелами или в другом написании. Поэтому
    поиск идёт по области, а не по одной ячейке. Если титула нет, файл всё
    равно обрабатывается: дата берётся из шапки.
    """
    for row in range(1, min(sheet.max_row, SCAN_ROWS) + 1):
        for column in range(1, min(sheet.max_column, SCAN_COLUMNS) + 1):
            text = _text(sheet.cell(row=row, column=column).value)
            if not text:
                continue
            folded = _fold(text)
            if any(mark in folded for mark in TITLE_MARKS):
                return row, text
    return None, ""


def date_from_text(text: str) -> str | None:
    """Дата из строки. Понимает «09.09.2026» и «09 сентября 2026 г.»."""
    match = DATE_PATTERN.search(text)
    if match:
        return match.group(1)
    word = WORD_DATE_PATTERN.search(text)
    if word:
        name = _fold(word.group(2))
        for stem, number in MONTHS.items():
            if name.startswith(stem):
                return f"{int(word.group(1)):02d}.{number:02d}.{word.group(3)}"
    return None


def find_date(sheet, limit: int = SCAN_ROWS) -> str | None:
    """Первая дата в шапке файла. Строки данных не просматриваются."""
    for row in range(1, min(sheet.max_row, limit) + 1):
        for column in range(1, min(sheet.max_column, SCAN_COLUMNS) + 1):
            found = date_from_text(_text(sheet.cell(row=row, column=column).value))
            if found:
                return found
    return None


def find_label_row(sheet, label: str, limit: int = SCAN_ROWS) -> int | None:
    needle = _fold(label)
    for row in range(1, min(sheet.max_row, limit) + 1):
        for column in range(1, min(sheet.max_column, SCAN_COLUMNS) + 1):
            if _fold(_text(sheet.cell(row=row, column=column).value)).startswith(needle):
                return row
    return None


def find_group_headers(sheet) -> list[int]:
    """Заголовок группы: A = «№» и C = «Хар-ка»."""
    rows = []
    for row in range(1, sheet.max_row + 1):
        code = _fold(_text(sheet.cell(row=row, column=COL_CODE).value))
        trait = _fold(_text(sheet.cell(row=row, column=COL_TRAIT).value))
        if code == HEADER_MARK_A and trait.startswith(_fold(HEADER_MARK_C)):
            rows.append(row)
    return rows


def _row_is_data(sheet, row: int) -> bool:
    code = sheet.cell(row=row, column=COL_CODE).value
    name = _text(sheet.cell(row=row, column=COL_NAME).value)
    if _text(code) == HEADER_MARK_A:
        return False
    return bool(_text(code)) or bool(name)


def _row_is_total(sheet, row: int) -> bool:
    if _text(sheet.cell(row=row, column=COL_CODE).value):
        return False
    if _text(sheet.cell(row=row, column=COL_NAME).value):
        return False
    return any(
        _is_number(sheet.cell(row=row, column=column).value)
        for column in (COL_FACT, COL_BOOK, COL_DIFF, COL_SUM_DIFF)
    )


def _group_name(sheet, header_row: int) -> str:
    for row in (header_row, header_row + 1, header_row - 1):
        name = _text(sheet.cell(row=row, column=COL_NAME).value)
        if name and name != HEADER_MARK_A:
            return name
    return f"Группа в строке {header_row}"


def build_group(sheet, header_row: int, limit_row: int) -> Group | None:
    first_data = None
    last_data = None
    total_row = None
    row = header_row + 1
    while row <= limit_row:
        if _row_is_data(sheet, row):
            if first_data is None:
                first_data = row
            last_data = row
        elif first_data is not None and _row_is_total(sheet, row):
            total_row = row
            break
        row += 1
    if first_data is None or total_row is None:
        return None
    return Group(
        name=_group_name(sheet, header_row),
        header_row=header_row,
        first_data_row=first_data,
        last_data_row=last_data or first_data,
        total_row=total_row,
    )


def parse(sheet) -> Document:
    """Собирает описание файла: титул, шапка, блоки групп."""
    title_row, title_text = find_title(sheet)
    number_match = NUMBER_PATTERN.search(title_text)
    doc_date = date_from_text(title_text)

    header_rows = find_group_headers(sheet)
    if not header_rows:
        sample = " | ".join(head_sample(sheet)[:6])
        raise ParseError(
            "В файле нет блоков групп товаров. Нужен заголовок со «№» в столбце A "
            f"и «Хар-ка» в столбце C. Начало листа: {sample or 'лист пустой'}"
        )
    if doc_date is None:
        doc_date = find_date(sheet, limit=max(1, header_rows[0] - 1))
    if doc_date is None:
        raise ParseError(
            "В файле нет даты вида ДД.ММ.ГГГГ. Дата нужна для имени выходного файла."
        )

    groups: list[Group] = []
    for index, header_row in enumerate(header_rows):
        limit = header_rows[index + 1] - 1 if index + 1 < len(header_rows) else sheet.max_row
        group = build_group(sheet, header_row, limit)
        if group is not None:
            groups.append(group)
    if not groups:
        raise ParseError("Ни у одной группы не найдена строка итога.")

    return Document(
        title_row=title_row,
        title_text=title_text,
        doc_number=number_match.group(1) if number_match else None,
        doc_date=doc_date,
        warehouse_row=find_label_row(sheet, "Склад:"),
        organization_row=find_label_row(sheet, "Организация:"),
        groups=groups,
    )
