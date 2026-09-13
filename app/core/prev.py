"""Чтение предыдущей инвентаризации для сравнения с текущей сверкой.

Предыдущая сверка — это готовый (уже разобранный) файл прошлой
инвентаризации. Из него нужны три вещи:

* позиции: название, количество расхождения, сумма и пометка в столбце C
  («не+», «перез»);
* признак «считалась в минус»: количество меньше нуля, а сумма не обнулена,
  то есть эту сумму списывали с продавцов;
* блок продавцов внизу файла: ФИО и вес (часы или единица), по которым
  сумма делилась в прошлый раз.

Позиции сопоставляются по полному названию товара (столбец B).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from .parse import COL_DIFF, COL_NAME, COL_SUM_DIFF, COL_TRAIT, parse

logger = logging.getLogger("excelkro.prev")

MARK_NOT_PLUS = "не+"
SPACES = re.compile(r"\s+")

# Подписи и заголовки мини-таблиц: это не продавцы.
SERVICE_LABELS = (
    "проверил",
    "администратор",
    "ревизоры",
    "ночной продавец",
    "перезачёт",
    "перезачет",
    "итого",
)
# Блок продавцов идёт сразу под итогом последней группы.
SELLER_SCAN_ROWS = 80


def name_key(value: object) -> str:
    """Ключ позиции: полное название без регистра и лишних пробелов."""
    text = str(value or "").replace("\xa0", " ").strip()
    return SPACES.sub(" ", text).lower()


def _number(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


@dataclass(frozen=True)
class PrevItem:
    """Позиция предыдущей инвентаризации."""

    name: str
    diff: float
    sum_diff: float
    mark: str

    @property
    def was_minus(self) -> bool:
        """Минус с неснятой суммой: её списывали с продавцов."""
        return self.diff < 0 and round(self.sum_diff, 2) != 0

    @property
    def was_not_plus(self) -> bool:
        """Позиция была помечена «не+»."""
        return MARK_NOT_PLUS in self.mark


@dataclass(frozen=True)
class PrevSeller:
    """Продавец предыдущей сверки и его вес при списании."""

    name: str
    weight: float = 0.0


@dataclass
class PrevSheet:
    """Разобранная предыдущая инвентаризация."""

    file_name: str = ""
    date: str = ""
    items: dict[str, PrevItem] = field(default_factory=dict)
    sellers: tuple[PrevSeller, ...] = ()

    @property
    def total_weight(self) -> float:
        return sum(seller.weight for seller in self.sellers)


def _merge(first: PrevItem, second: PrevItem) -> PrevItem:
    """Одно и то же название встретилось дважды: складываем."""
    marks = " ".join(part for part in (first.mark, second.mark) if part)
    return PrevItem(
        name=first.name,
        diff=first.diff + second.diff,
        sum_diff=first.sum_diff + second.sum_diff,
        mark=marks,
    )


def _is_formula(value: object) -> bool:
    return isinstance(value, str) and value.startswith("=")


def read_sellers(sheet, start_row: int) -> tuple[PrevSeller, ...]:
    """Блок продавцов: строка ФИО, под ней строка веса."""
    result: list[PrevSeller] = []
    row = start_row
    limit = min(sheet.max_row, start_row + SELLER_SCAN_ROWS)
    while row <= limit:
        value = sheet.cell(row=row, column=COL_NAME).value
        text = str(value or "").strip()
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not text or numeric or _is_formula(value) or name_key(text) in SERVICE_LABELS:
            row += 1
            continue
        below = sheet.cell(row=row + 1, column=COL_NAME).value
        result.append(PrevSeller(name=text, weight=_number(below)))
        row += 2
    return tuple(result)


def read(path: str | Path, file_name: str = "", sheet_name: str = "TDSheet") -> PrevSheet:
    """Читает предыдущую сверку. Ошибки разбора пробрасываются наверх."""
    workbook = openpyxl.load_workbook(path, data_only=True)
    if sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
    else:
        sheet = workbook[workbook.sheetnames[0]]

    document = parse(sheet)
    items: dict[str, PrevItem] = {}
    for group in document.groups:
        for row in group.data_rows:
            raw = sheet.cell(row=row, column=COL_NAME).value
            key = name_key(raw)
            if not key:
                continue
            item = PrevItem(
                name=str(raw or "").strip(),
                diff=_number(sheet.cell(row=row, column=COL_DIFF).value),
                sum_diff=_number(sheet.cell(row=row, column=COL_SUM_DIFF).value),
                mark=str(sheet.cell(row=row, column=COL_TRAIT).value or "").strip().lower(),
            )
            found = items.get(key)
            items[key] = _merge(found, item) if found is not None else item

    last_total = max(group.total_row for group in document.groups)
    sellers = read_sellers(sheet, last_total)
    logger.info(
        "Предыдущая сверка: позиций %s, продавцов %s", len(items), len(sellers)
    )
    return PrevSheet(
        file_name=file_name or Path(path).name,
        date=document.doc_date or "",
        items=items,
        sellers=sellers,
    )
