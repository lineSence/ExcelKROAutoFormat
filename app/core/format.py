"""Шаги 3–9: сдвиг шапки, пересорты, итоги, геометрия."""

from __future__ import annotations

from dataclasses import dataclass, field

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import style
from .parse import (
    COL_CODE,
    COL_DIFF,
    COL_NAME,
    COL_SUM_DIFF,
    COL_TRAIT,
    Document,
    Group,
    parse,
)
from .resort import (
    DECISION_RESORT,
    DECISION_SHORTAGE,
    DECISION_SURPLUS,
    Cluster,
    DoubtfulPair,
    build_clusters,
    make_item,
)

MARK_NOT_PLUS = "не+"
COL_CURRENCY_LABEL = 8   # H
COL_GRAND_TOTAL = 9      # I


@dataclass
class GroupResult:
    """Итог разбора одной группы."""

    name: str
    clusters: list[Cluster] = field(default_factory=list)
    doubtful: list[DoubtfulPair] = field(default_factory=list)
    zeroed_sum: float = 0.0
    shortage_sum: float = 0.0
    resort_pieces: float = 0.0
    single_rows: int = 0
    total_cell: str = ""


@dataclass
class FormatResult:
    document: Document
    warehouse: str
    groups: list[GroupResult] = field(default_factory=list)
    last_row: int = 0


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def shift_header(sheet, document: Document) -> Document:
    """Шаг 3. Удаляет строку «Организация:» и поднимает шапку."""
    row = document.organization_row
    if row is None:
        return document
    for merged in list(sheet.merged_cells.ranges):
        if merged.min_row <= row <= merged.max_row:
            sheet.unmerge_cells(str(merged))
    sheet.delete_rows(row, 1)
    return parse(sheet)


def fill_header(sheet, warehouse: str) -> None:
    """Шаг 3.3 и 3.4: имя склада и очистка валюты."""
    cell = sheet.cell(row=4, column=COL_TRAIT)
    cell.value = warehouse
    cell.font = Font(name="Arial", size=9, bold=True)
    for column in range(COL_GRAND_TOTAL, COL_GRAND_TOTAL + 2):
        neighbour = sheet.cell(row=4, column=column)
        if str(neighbour.value or "").strip().lower() == "руб":
            neighbour.value = None
    label = sheet.cell(row=4, column=COL_CURRENCY_LABEL)
    if str(label.value or "").strip().startswith("Валюта"):
        right = sheet.cell(row=4, column=COL_CURRENCY_LABEL + 1)
        if str(right.value or "").strip().lower() == "руб":
            right.value = None


def process_group(sheet, group: Group, settings) -> GroupResult:
    """Шаг 4: грозди брендов и разбор расхождений."""
    items = []
    for row in group.data_rows:
        diff = _number(sheet.cell(row=row, column=COL_DIFF).value)
        if diff == 0:
            continue
        items.append(
            make_item(
                row=row,
                name=sheet.cell(row=row, column=COL_NAME).value or "",
                diff=diff,
                sum_diff=_number(sheet.cell(row=row, column=COL_SUM_DIFF).value),
                type_words=settings.type_words,
            )
        )

    clusters, doubtful = build_clusters(
        items,
        settings.similarity_threshold,
        settings.doubtful_min,
        settings.doubtful_max,
    )

    result = GroupResult(name=group.name, clusters=clusters, doubtful=doubtful)
    for cluster in clusters:
        if len(cluster.items) == 1:
            result.single_rows += 1
        if cluster.decision == DECISION_RESORT:
            result.resort_pieces += sum(abs(item.diff) for item in cluster.items) / 2
            for item in cluster.items:
                style.frame(sheet.cell(row=item.row, column=COL_DIFF))
                _zero_sum(sheet, item, result)
        elif cluster.decision == DECISION_SURPLUS:
            for item in cluster.items:
                style.paint(sheet.cell(row=item.row, column=COL_DIFF), style.ORANGE)
                sheet.cell(row=item.row, column=COL_TRAIT).value = MARK_NOT_PLUS
                _zero_sum(sheet, item, result)
        elif cluster.decision == DECISION_SHORTAGE:
            for item in cluster.items:
                if item.diff < 0:
                    style.paint(sheet.cell(row=item.row, column=COL_DIFF), style.YELLOW)
                    style.paint(sheet.cell(row=item.row, column=COL_SUM_DIFF), style.YELLOW)
                else:
                    style.frame(sheet.cell(row=item.row, column=COL_DIFF))
                    _zero_sum(sheet, item, result)

    result.shortage_sum = sum(
        _number(sheet.cell(row=row, column=COL_SUM_DIFF).value) for row in group.data_rows
    )
    return result


def _zero_sum(sheet, item, result: GroupResult) -> None:
    cell = sheet.cell(row=item.row, column=COL_SUM_DIFF)
    result.zeroed_sum += _number(cell.value)
    cell.value = 0


def highlight_rest(sheet, group: Group) -> None:
    """Шаг 7: подсветка остальных расхождений."""
    for row in group.data_rows:
        diff = _number(sheet.cell(row=row, column=COL_DIFF).value)
        sum_cell = sheet.cell(row=row, column=COL_SUM_DIFF)
        if diff == 0 and _number(sum_cell.value) != 0:
            style.paint(sheet.cell(row=row, column=COL_DIFF), style.YELLOW)
            style.paint(sum_cell, style.YELLOW)


def write_group_total(sheet, group: Group) -> str:
    """Шаг 5: формула итога группы с диапазоном до строки перед итогом."""
    for column in (4, 5, COL_DIFF, 11):
        sheet.cell(row=group.total_row, column=column).value = None
    last = max(group.first_data_row, group.total_row - 1)
    letter = get_column_letter(COL_SUM_DIFF)
    cell = sheet.cell(row=group.total_row, column=COL_SUM_DIFF)
    cell.value = f"=SUM({letter}{group.first_data_row}:{letter}{last})"
    style.style_total_cell(cell)
    return f"{letter}{group.total_row}"


def write_grand_total(sheet, total_cells: list[str]) -> None:
    """Шаг 6: общий итог в I4."""
    cell = sheet.cell(row=4, column=COL_GRAND_TOTAL)
    cell.value = f"=SUM({','.join(total_cells)})"
    style.style_grand_total_cell(cell)


def format_workbook(sheet, warehouse: str, settings) -> FormatResult:
    """Полный проход шагов 2–9."""
    document = parse(sheet)
    document = shift_header(sheet, document)
    fill_header(sheet, warehouse)

    result = FormatResult(document=document, warehouse=warehouse)
    total_cells: list[str] = []
    for group in document.groups:
        group_result = process_group(sheet, group, settings)
        highlight_rest(sheet, group)
        group_result.total_cell = write_group_total(sheet, group)
        total_cells.append(group_result.total_cell)
        result.groups.append(group_result)

    if total_cells:
        write_grand_total(sheet, total_cells)

    result.last_row = max(group.total_row for group in document.groups)
    style.apply_geometry(sheet, result.last_row)
    return result


def output_filename(warehouse: str, doc_date: str | None) -> str:
    """Шаг 9: имя выходного файла."""
    safe_name = (warehouse or "Склад").strip() or "Склад"
    date_text = doc_date or "без даты"
    return f"{safe_name} {date_text}.xlsx"
