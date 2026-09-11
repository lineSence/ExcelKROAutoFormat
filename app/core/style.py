"""Цвета, рамки, ширины и высоты готового файла."""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

YELLOW = "FFFF00"
ORANGE = "FFC000"
GREEN = "92D050"
BLACK = "FF000000"

MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "DD.MM.YYYY"

COLUMN_WIDTHS = {
    "A": 5.83,
    "B": 55.83,
    "C": 5.83,
    "G": 10.83,
    "K": 61.33,
    "L": 55.83,
}
ROW_HEIGHT = 12


def fill(color: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=color, end_color=color)


def medium_border() -> Border:
    side = Side(style="medium", color=BLACK)
    return Border(left=side, right=side, top=side, bottom=side)


def paint(cell, color: str) -> None:
    cell.fill = fill(color)


def frame(cell) -> None:
    cell.border = medium_border()


def style_total_cell(cell) -> None:
    """Итог группы в столбце J."""
    cell.font = Font(name="Arial", size=9, bold=True)
    cell.number_format = MONEY_FORMAT
    cell.border = medium_border()
    cell.alignment = Alignment(horizontal="center", vertical="center")


def style_grand_total_cell(cell) -> None:
    """Общий итог I4."""
    cell.font = Font(name="Arial", size=10, bold=True)
    cell.number_format = MONEY_FORMAT
    cell.border = medium_border()
    cell.alignment = Alignment(horizontal="center", vertical="center")
    paint(cell, YELLOW)


def apply_geometry(sheet, last_row: int) -> None:
    """Шаг 8: ширины, высоты, автофильтр, параметры печати."""
    for letter, width in COLUMN_WIDTHS.items():
        sheet.column_dimensions[letter].width = width
    for row in range(1, last_row + 1):
        sheet.row_dimensions[row].height = ROW_HEIGHT
    sheet.auto_filter.ref = f"K1:K{last_row}"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
