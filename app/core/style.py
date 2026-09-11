"""Цвета, рамки, ширины и высоты готового файла."""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

YELLOW = "FFFFFF00"
ORANGE = "FFFFC000"
GREEN = "FF92D050"
BLACK = "FF000000"

# В образце формат без разделителя тысяч.
MONEY_FORMAT = "0.00"
DATE_FORMAT = "DD.MM.YYYY"

COLUMN_WIDTHS = {
    "A": 5.83,
    "B": 55.83,
    "C": 5.83,
    "D": 13.0,
    "E": 13.0,
    "F": 13.0,
    "G": 10.83,
    "H": 13.0,
    "I": 13.0,
    "J": 13.0,
    "K": 61.33,
    "L": 55.83,
}
ROW_HEIGHT = 12


def fill(color: str) -> PatternFill:
    return PatternFill(fill_type="solid", start_color=color, end_color=color)


def medium_side() -> Side:
    return Side(style="medium", color=BLACK)


def medium_border() -> Border:
    side = medium_side()
    return Border(left=side, right=side, top=side, bottom=side)


def paint(cell, color: str) -> None:
    cell.fill = fill(color)


def frame(cell) -> None:
    cell.border = medium_border()


def frame_block(sheet, rows, column: int) -> None:
    """Ставит толстую рамку по контуру блока строк.

    Соседние строки собираются в один блок. Внутри блока
    горизонтальных линий нет, как в образце.
    """
    ordered = sorted(set(int(row) for row in rows))
    if not ordered:
        return
    side = medium_side()
    runs: list[list[int]] = []
    for row in ordered:
        if runs and row == runs[-1][-1] + 1:
            runs[-1].append(row)
        else:
            runs.append([row])
    for run in runs:
        for row in run:
            cell = sheet.cell(row=row, column=column)
            cell.border = Border(
                left=side,
                right=side,
                top=side if row == run[0] else None,
                bottom=side if row == run[-1] else None,
            )


def style_total_cell(cell) -> None:
    """Итог группы в столбце J."""
    cell.font = Font(name="Arial", size=9, bold=True)
    cell.number_format = MONEY_FORMAT
    cell.border = medium_border()
    cell.alignment = Alignment(horizontal="center", vertical="center")


def style_grand_total_cell(cell) -> None:
    """Общий итог в столбце I."""
    cell.font = Font(name="Arial", size=10, bold=True)
    cell.number_format = MONEY_FORMAT
    cell.border = medium_border()
    cell.alignment = Alignment(horizontal="center", vertical="center")
    paint(cell, YELLOW)


def apply_geometry(sheet, last_row: int) -> None:
    """Шаг 8: ширины, высоты, автофильтр, параметры печати."""
    for letter, width in COLUMN_WIDTHS.items():
        sheet.column_dimensions[letter].width = width
    for row in range(1, max(last_row, sheet.max_row) + 1):
        sheet.row_dimensions[row].height = ROW_HEIGHT
    sheet.auto_filter.ref = f"K1:K{last_row}"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
