"""Шаг 10. Ручные поля сверки: причина, дата, неучтёнка, продавцы и подписи.

В образцах эти данные вносятся руками после разбора пересортов:

* причина инвентаризации — столбец C в строке под «Склад:»;
* дата предыдущей инвентаризации — столбец B под титулом (в образце B4),
  выравнивание по левому краю, иначе Excel прижимает дату вправо;
* сумма неучтёнки — задаётся в интерфейсе и пишется в ячейку C1
  (сам блок рисует модуль `format`);
* продавцы — блок внизу файла: строка ФИО, под ней строка с весом
  (часы или единица при делении поровну), рядом формула доли;
  в конце — итог весов.

Ставка продавцов считается от итоговой суммы с вычетом неучтёнки —
то есть от зелёной ячейки «С н. д/с:» строкой ниже общего итога.

Под блоком продавцов может стоять мини-таблица «Перезачёт» в том же
оформлении: продавцы предыдущей сверки и их доли перезачёта.

Остальные подписи (проверил, администратор, ревизоры, ночной продавец)
собраны в мини-таблицу ниже.

Модуль не знает ни про HTTP, ни про пересорты: набор полей легко
расширить под другие задачи — добавьте поле в SheetMeta и строку
в signature_rows() или свой шаг в apply().
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.styles.colors import Color
from openpyxl.utils.cell import range_boundaries

from . import style
from .parse import COL_NAME, COL_TRAIT, Document

COL_VALUE_START = COL_TRAIT       # C — значение мини-таблицы
COL_VALUE_END = 7                 # G — ФИО целиком влезает в C:G
COL_NET_TOTAL = 9                 # I — итог с вычетом неучтёнки
# Зелёная ячейка «С н. д/с:» стоит строкой ниже общего итога.
NET_ROW_OFFSET = 1
# Строка со словом «Склад:» в новой раскладке пятая.
DEFAULT_WAREHOUSE_ROW = 5
GAP_BEFORE_SIGNATURES = 1         # пустая строка между блоками
NIGHT_ABSENT = "нет"
TRUE_WORDS = ("1", "true", "yes", "on", "да")
CREDIT_TITLE = "Перезачёт"

# Способы разнести недостачу между продавцами.
SHARE_HOURS = "hours"
SHARE_EQUAL = "equal"
SHARE_MODES = (SHARE_HOURS, SHARE_EQUAL)
EQUAL_WEIGHT = 1

FONT = Font(name="Arial", size=9)
FONT_BOLD = Font(name="Arial", size=9, bold=True)

# Дата предыдущей инвентаризации в образце прижата влево.
PREV_DATE_ALIGN = Alignment(horizontal="left", vertical="center")

# Оформление блока продавцов повторяет образец один в один:
# Arial 8, ФИО на светло-зелёной заливке (индекс 43 старой палитры 1С),
# тонкие рамки цвета из индекса 60, числа веса — вправо с отступом.
SELLER_FONT = Font(name="Arial", size=8)
SELLER_FILL = PatternFill(fill_type="solid", fgColor=Color(indexed=43))
SELLER_BORDER = Border(
    left=Side(style="thin", color=Color(indexed=60)),
    right=Side(style="thin", color=Color(indexed=60)),
    top=Side(style="thin", color=Color(indexed=60)),
    bottom=Side(style="thin", color=Color(indexed=60)),
)
SELLER_NAME_ALIGN = Alignment(vertical="top", wrap_text=True)
SELLER_WEIGHT_ALIGN = Alignment(
    horizontal="right", vertical="top", wrap_text=True, indent=2
)
SELLER_TOTAL_ALIGN = Alignment(horizontal="right", vertical="top", wrap_text=True)
SELLER_SHARE_ALIGN = Alignment(horizontal="left")
SELLER_ROW_HEIGHT = 12


def unmerge_cell(sheet, row: int, column: int) -> None:
    """Снимает объединение, если ячейка входит в него."""
    for merged in [str(item) for item in sheet.merged_cells.ranges]:
        min_col, min_row, max_col, max_row = range_boundaries(merged)
        if min_row <= row <= max_row and min_col <= column <= max_col:
            sheet.unmerge_cells(merged)


def _thin_border() -> Border:
    side = Side(style="thin", color=style.BLACK)
    return Border(left=side, right=side, top=side, bottom=side)


def _items(value: object) -> list[str]:
    """Значения поля: список из формы или текст с разделителями."""
    if isinstance(value, (list, tuple)):
        return [str(item or "").strip() for item in value]
    return [item.strip() for item in re.split(r"[\n;]+", str(value or ""))]


def _lines(value: object) -> tuple[str, ...]:
    """Непустые значения поля, порядок сохраняется."""
    return tuple(item for item in _items(value) if item)


def _hours(value: object) -> str:
    """Часы как текст: принимаем запятую и лишние пробелы."""
    plain = str(value or "").strip().replace(",", ".")
    if not plain:
        return ""
    try:
        number = float(plain)
    except ValueError:
        return ""
    if number <= 0:
        return ""
    return str(int(number)) if number == int(number) else str(number)


def _hours_number(value: str) -> float | int | None:
    """Часы для записи в ячейку."""
    if not value:
        return None
    number = float(value)
    return int(number) if number == int(number) else number


def _amount(value: object) -> float | int | None:
    """Сумма из формы: принимаем запятую и пробелы внутри числа."""
    plain = str(value or "").strip().replace(",", ".").replace(" ", "")
    if not plain:
        return None
    try:
        number = float(plain)
    except ValueError:
        return None
    return int(number) if number == int(number) else number


def _share_mode(value: object) -> str:
    """Способ распределения. По умолчанию — по часам."""
    text = str(value or "").strip().lower()
    return text if text in SHARE_MODES else SHARE_HOURS


def _date_value(text: object) -> date | None:
    """Дата из формы: 2026-08-17 или 17.08.2026."""
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    plain = str(text or "").strip()
    if not plain:
        return None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(plain, pattern).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Seller:
    """Продавец и его отработанные часы."""

    name: str = ""
    hours: str = ""

    def to_form(self) -> dict:
        return {"name": self.name, "hours": self.hours}


def _sellers(names: object, hours: object) -> tuple[Seller, ...]:
    """Пары «ФИО — часы» из двух параллельных полей формы."""
    name_list = _items(names)
    hour_list = _items(hours)
    result: list[Seller] = []
    for index, name in enumerate(name_list):
        if not name:
            continue
        raw = hour_list[index] if index < len(hour_list) else ""
        result.append(Seller(name=name, hours=_hours(raw)))
    return tuple(result)


@dataclass
class SheetMeta:
    """Ручные поля одной сверки."""

    reason: str = ""
    prev_date: str = ""
    # Сумма неучтёнки: задаётся в интерфейсе, хранится текстом.
    extra: str = ""
    share_mode: str = SHARE_HOURS
    sellers: tuple[Seller, ...] = field(default_factory=tuple)
    checked_by: str = ""
    admin: str = ""
    auditors: tuple[str, ...] = field(default_factory=tuple)
    night: bool = False
    night_name: str = ""

    @classmethod
    def from_form(cls, form: dict) -> "SheetMeta":
        """Собирает поля из формы или из сохранённого словаря."""
        night = str(form.get("night") or "").strip().lower() in TRUE_WORDS
        return cls(
            reason=str(form.get("reason") or "").strip(),
            prev_date=str(form.get("prev_date") or "").strip(),
            extra=str(form.get("extra") or "").strip(),
            share_mode=_share_mode(form.get("share_mode")),
            sellers=_sellers(form.get("sellers"), form.get("seller_hours")),
            checked_by=str(form.get("checked_by") or "").strip(),
            admin=str(form.get("admin") or "").strip(),
            auditors=_lines(form.get("auditors")),
            night=night,
            night_name=str(form.get("night_name") or "").strip(),
        )

    def to_form(self) -> dict:
        """Обратно в вид для шаблона: каждая запись — отдельная строка."""
        return {
            "reason": self.reason,
            "prev_date": self.prev_date,
            "extra": self.extra,
            "share_mode": self.share_mode,
            "by_hours": self.share_mode == SHARE_HOURS,
            "sellers": [seller.to_form() for seller in self.sellers],
            "checked_by": self.checked_by,
            "admin": self.admin,
            "auditors": list(self.auditors),
            "night": self.night,
            "night_name": self.night_name,
        }

    @property
    def extra_value(self) -> float | int | None:
        """Сумма неучтёнки для ячейки C1. Пустое поле — пустая ячейка."""
        return _amount(self.extra)

    @property
    def filled(self) -> bool:
        """Есть ли хоть одно заполненное поле."""
        return any(
            (
                self.reason,
                self.prev_date,
                self.extra,
                self.sellers,
                self.checked_by,
                self.admin,
                self.auditors,
                self.night,
                self.night_name,
            )
        )

    def weight(self, seller: Seller) -> float | int | None:
        """Вес продавца: часы или единица при делении поровну."""
        if self.share_mode == SHARE_EQUAL:
            return EQUAL_WEIGHT
        return _hours_number(seller.hours)

    def signature_rows(self) -> list[tuple[str, str]]:
        """Мини-таблица подписей: пары «название — значение»."""
        rows: list[tuple[str, str]] = []
        if self.checked_by:
            rows.append(("Проверил", self.checked_by))
        if self.admin:
            rows.append(("Администратор", self.admin))
        for index, name in enumerate(self.auditors):
            rows.append(("Ревизоры" if index == 0 else "", name))
        if self.night:
            rows.append(("Ночной продавец", self.night_name or "да"))
        elif rows:
            rows.append(("Ночной продавец", NIGHT_ABSENT))
        return rows


def _write(sheet, row: int, column: int, value, bold: bool = False) -> None:
    unmerge_cell(sheet, row, column)
    cell = sheet.cell(row=row, column=column)
    cell.value = value
    cell.font = FONT_BOLD if bold else FONT


def write_reason(sheet, document: Document, reason: str) -> None:
    """Причина инвентаризации — как в образце, под «Склад:»."""
    if not reason:
        return
    row = (document.warehouse_row or DEFAULT_WAREHOUSE_ROW) + 1
    _write(sheet, row, COL_TRAIT, reason, bold=True)


def write_prev_date(sheet, document: Document, text: str) -> None:
    """Дата предыдущей инвентаризации — столбец B под титулом.

    В образце дата прижата влево, поэтому выравнивание задаётся явно:
    Excel сам прижимает даты вправо, как любое другое число.
    """
    value = _date_value(text)
    if value is None:
        return
    row = document.title_row + 1
    _write(sheet, row, COL_NAME, value)
    cell = sheet.cell(row=row, column=COL_NAME)
    cell.number_format = style.DATE_FORMAT
    cell.alignment = PREV_DATE_ALIGN


def _weight_format(value) -> str:
    """В образце веса целые, но допускаем получасы."""
    if isinstance(value, float) and value != int(value):
        return "0.00"
    return "0"


def _style_seller_cell(sheet, row: int, column: int, kind: str) -> None:
    """Один в один повторяет оформление таблицы продавцов из образца."""
    cell = sheet.cell(row=row, column=column)
    cell.font = SELLER_FONT
    if kind == "name":
        cell.fill = SELLER_FILL
        cell.border = SELLER_BORDER
        cell.alignment = SELLER_NAME_ALIGN
    elif kind == "weight":
        cell.border = SELLER_BORDER
        cell.alignment = SELLER_WEIGHT_ALIGN
        cell.number_format = _weight_format(cell.value)
    elif kind == "total":
        cell.border = SELLER_BORDER
        cell.alignment = SELLER_TOTAL_ALIGN
        cell.number_format = "0"
    else:  # формулы доли в столбце C — без рамок и заливки
        cell.alignment = SELLER_SHARE_ALIGN
    sheet.row_dimensions[row].height = SELLER_ROW_HEIGHT


def grand_total_row(sheet, document: Document) -> int:
    """Строка с общим итогом по складу в столбце I.

    Итог по группам всегда стоит в строке со словом «Склад:» (в образце
    I5), а строкой ниже — зелёная ячейка «С н. д/с:» (I6) с итогом
    без неучтёнки. Именно от неё считается ставка продавцов.
    """
    return document.warehouse_row or DEFAULT_WAREHOUSE_ROW


def write_sellers(sheet, document: Document, first_row: int, data: SheetMeta) -> int:
    """Блок продавцов. Возвращает номер последней занятой строки.

    Под каждым ФИО стоит вес: отработанные часы или единица,
    если недостачу делят поровну. Рядом формула доли, внизу — итог.
    Пустые часы оставляем для ручного ввода.
    """
    sellers = data.sellers
    if not sellers:
        return first_row - 1

    net_row = grand_total_row(sheet, document) + NET_ROW_OFFSET
    number_rows = [first_row + 1 + index * 2 for index in range(len(sellers))]
    total_row = number_rows[-1] + 1

    for index, seller in enumerate(sellers):
        name_row = first_row + index * 2
        number_row = number_rows[index]
        _write(sheet, name_row, COL_NAME, seller.name)
        _style_seller_cell(sheet, name_row, COL_NAME, "name")
        if index == 0:
            # Ставка на единицу веса: итог с вычетом неучтёнки
            # (зелёная ячейка «С н. д/с:») делится на сумму весов.
            _write(sheet, name_row, COL_TRAIT, f"=I{net_row}/B{total_row}")
            _style_seller_cell(sheet, name_row, COL_TRAIT, "share")
        weight = data.weight(seller)
        if weight is not None:
            _write(sheet, number_row, COL_NAME, weight)
        _style_seller_cell(sheet, number_row, COL_NAME, "weight")
        _write(sheet, number_row, COL_TRAIT, f"=C{first_row}*B{number_row}")
        _style_seller_cell(sheet, number_row, COL_TRAIT, "share")

    _write(
        sheet,
        total_row,
        COL_NAME,
        "=SUM({})".format(",".join(f"B{row}" for row in number_rows)),
    )
    _style_seller_cell(sheet, total_row, COL_NAME, "total")
    return total_row


def write_credits(sheet, first_row: int, shares) -> int:
    """Мини-таблица «Перезачёт» под блоком продавцов.

    Оформление то же, что у продавцов: заголовок и ФИО на заливке,
    под каждым ФИО — сумма перезачёта, внизу итог.
    Суммы считаются по весам продавцов предыдущей сверки.
    """
    rows = [(str(name or ""), float(amount)) for name, amount in (shares or [])]
    if not rows:
        return first_row - 1

    _write(sheet, first_row, COL_NAME, CREDIT_TITLE, bold=True)
    _style_seller_cell(sheet, first_row, COL_NAME, "name")

    amount_rows: list[int] = []
    row = first_row
    for name, amount in rows:
        row += 1
        _write(sheet, row, COL_NAME, name)
        _style_seller_cell(sheet, row, COL_NAME, "name")
        row += 1
        _write(sheet, row, COL_NAME, round(amount, 2))
        _style_seller_cell(sheet, row, COL_NAME, "weight")
        sheet.cell(row=row, column=COL_NAME).number_format = style.MONEY_FORMAT
        amount_rows.append(row)

    row += 1
    _write(
        sheet,
        row,
        COL_NAME,
        "=SUM({})".format(",".join(f"B{item}" for item in amount_rows)),
    )
    _style_seller_cell(sheet, row, COL_NAME, "total")
    sheet.cell(row=row, column=COL_NAME).number_format = style.MONEY_FORMAT
    return row


def write_signatures(sheet, first_row: int, rows: list[tuple[str, str]]) -> int:
    """Мини-таблица под блоком продавцов. Возвращает последнюю строку."""
    if not rows:
        return first_row - 1
    border = _thin_border()
    for index, (label, value) in enumerate(rows):
        row = first_row + index
        _write(sheet, row, COL_NAME, label, bold=True)
        _write(sheet, row, COL_VALUE_START, value)
        unmerge_cell(sheet, row, COL_VALUE_END)
        sheet.merge_cells(
            start_row=row,
            start_column=COL_VALUE_START,
            end_row=row,
            end_column=COL_VALUE_END,
        )
        sheet.cell(row=row, column=COL_NAME).border = border
        for column in range(COL_VALUE_START, COL_VALUE_END + 1):
            cell = sheet.cell(row=row, column=column)
            cell.border = border
            cell.alignment = Alignment(horizontal="left", vertical="center")
    return first_row + len(rows) - 1


def apply(
    sheet,
    document: Document,
    data: SheetMeta | None,
    last_row: int,
    credits=None,
) -> int:
    """Заполняет все ручные поля. Возвращает номер последней строки.

    `credits` — доли перезачёта из сравнения с предыдущей инвентаризацией.
    Таблица «Перезачёт» пишется даже тогда, когда ручные поля пустые.
    """
    shares = list(credits or [])
    if (data is None or not data.filled) and not shares:
        return last_row
    if data is None:
        data = SheetMeta()

    write_reason(sheet, document, data.reason)
    write_prev_date(sheet, document, data.prev_date)

    # Блок продавцов начинается в строке последнего итога, как в образце.
    bottom = write_sellers(sheet, document, last_row, data)
    if shares:
        start = max(bottom, last_row) + 1 + GAP_BEFORE_SIGNATURES
        bottom = write_credits(sheet, start, shares)
    signatures = data.signature_rows()
    if signatures:
        start = max(bottom, last_row) + 1 + GAP_BEFORE_SIGNATURES
        bottom = write_signatures(sheet, start, signatures)
    return max(bottom, last_row)
