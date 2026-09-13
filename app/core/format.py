"""Шаги 3–10: сдвиг шапки, пересорты, итоги, ручные поля, геометрия."""

from __future__ import annotations

from dataclasses import dataclass, field

from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from . import prev as prev_book
from . import style
from .meta import EXTRA_ROW_OFFSET, SheetMeta
from .meta import apply as apply_meta
from .parse import (
    COL_BOOK,
    COL_CODE,
    COL_DIFF,
    COL_DOC,
    COL_FACT,
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
from .verify import load_verifier

MARK_NOT_PLUS = "не+"
# Пометки сравнения с предыдущей инвентаризацией: пишутся в тот же столбец C.
MARK_NOT_MINUS = "не-"
MARK_CREDIT = "перез"
COL_CURRENCY_LABEL = 8   # H
COL_GRAND_TOTAL = 9      # I
TITLE_TARGET_ROW = 2     # В образце титул стоит во второй строке.
SCAN_COLUMNS = 12

# В строке итога группы числа 1С не нужны: остаётся только сумма в J.
TOTAL_ROW_CLEAR_COLUMNS = (COL_FACT, COL_BOOK, COL_DIFF, COL_DOC)

EXCLUDED_NOTE = (
    "Позиции с пометками «перез» и «не-» исключены из подбора пересортов: "
    "их пары подбирались заново или остались без пары."
)


@dataclass
class GroupResult:
    """Итог разбора одной группы."""

    name: str
    clusters: list[Cluster] = field(default_factory=list)
    doubtful: list[DoubtfulPair] = field(default_factory=list)
    zeroed_sum: float = 0.0
    # Остаток сумм группы после разбора (может быть и положительным).
    remainder_sum: float = 0.0
    resort_pieces: float = 0.0
    single_rows: int = 0
    total_cell: str = ""
    # Соответствие «старая строка → новая строка» после перестановки гроздей.
    moved: dict[int, int] = field(default_factory=dict)

    @property
    def shortage_sum(self) -> float:
        """Старое имя поля `remainder_sum`."""
        return self.remainder_sum


@dataclass
class Comparison:
    """Итог сравнения с предыдущей инвентаризацией."""

    file_name: str = ""
    date: str = ""
    matched: int = 0
    # Позиции, получившие «перез» (были в минусе, стали в плюсе).
    credits: list[dict] = field(default_factory=list)
    # Позиции, получившие «не-» (были «не+», стали в минусе).
    not_minus: list[dict] = field(default_factory=list)
    # Сумма перезачёта: столько списывали за эти позиции в прошлый раз.
    credit_sum: float = 0.0
    # Пары «продавец прошлой сверки — его доля перезачёта».
    shares: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def marked_rows(self) -> int:
        return len(self.credits) + len(self.not_minus)


@dataclass
class FormatResult:
    document: Document
    warehouse: str
    groups: list[GroupResult] = field(default_factory=list)
    last_row: int = 0
    # Работал ли второй слой проверки.
    verify_used: bool = False
    # Последняя строка с учётом ручных блоков (продавцы, подписи).
    bottom_row: int = 0
    # Сравнение с предыдущей инвентаризацией.
    comparison: Comparison = field(default_factory=Comparison)


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _row_is_empty(sheet, row: int) -> bool:
    return all(
        sheet.cell(row=row, column=column).value in (None, "")
        for column in range(1, SCAN_COLUMNS + 1)
    )


def _shift_range(text: str, deleted: list[int]) -> str | None:
    """Пересчитывает адрес объединения после удаления строк."""
    min_col, min_row, max_col, max_row = range_boundaries(text)
    if any(min_row <= row <= max_row for row in deleted):
        return None
    top = min_row - sum(1 for row in deleted if row < min_row)
    bottom = max_row - sum(1 for row in deleted if row < max_row)
    left = get_column_letter(min_col)
    right = get_column_letter(max_col)
    return f"{left}{top}:{right}{bottom}"


def drop_rows(sheet, rows: list[int]) -> None:
    """Удаляет строки без потери данных.

    openpyxl сдвигает объединённые ячейки неверно и теряет первую
    строку данных группы. Поэтому сначала снимаем все объединения,
    потом удаляем строки, потом ставим объединения на новые места.
    """
    targets = sorted({int(row) for row in rows if row})
    if not targets:
        return
    ranges = [str(item) for item in sheet.merged_cells.ranges]
    for text in ranges:
        sheet.unmerge_cells(text)
    for row in reversed(targets):
        sheet.delete_rows(row, 1)
    for text in ranges:
        moved = _shift_range(text, targets)
        if moved:
            sheet.merge_cells(moved)


def shift_header(sheet, document: Document) -> Document:
    """Шаг 3. Убирает строку «Организация:» и лишние строки сверху."""
    rows: list[int] = []
    if document.organization_row:
        rows.append(document.organization_row)

    # В образце над титулом остаётся одна пустая строка.
    extra = document.title_row - TITLE_TARGET_ROW
    row = 1
    while extra > 0 and row < document.title_row:
        if _row_is_empty(sheet, row) and row not in rows:
            rows.append(row)
            extra -= 1
        row += 1

    if not rows:
        return document
    drop_rows(sheet, rows)
    return parse(sheet)


def fill_header(sheet, document: Document, warehouse: str) -> int:
    """Шаг 3.3 и 3.4: имя склада и очистка валюты.

    Всё пишется в строку со словом «Склад:», как в образце.
    """
    row = document.warehouse_row or 4
    unmerge_cell(sheet, row, COL_TRAIT)
    cell = sheet.cell(row=row, column=COL_TRAIT)
    cell.value = warehouse
    cell.font = Font(name="Arial", size=9, bold=True)
    for column in range(COL_CURRENCY_LABEL, SCAN_COLUMNS + 1):
        neighbour = sheet.cell(row=row, column=column)
        if str(neighbour.value or "").strip().lower() == "руб":
            neighbour.value = None
    return row


def _row_snapshot(sheet, row: int) -> list[tuple]:
    """Снимок строки: значения и оформление всех ячеек."""
    cells = []
    for column in range(1, SCAN_COLUMNS + 1):
        cell = sheet.cell(row=row, column=column)
        cells.append((cell.value, style.read_style(cell)))
    return cells


def cluster_order(group: Group, clusters: list[Cluster]) -> list[int]:
    """Новый порядок строк: члены одной грозди идут рядом."""
    members: dict[int, list[int]] = {}
    for cluster in clusters:
        if len(cluster.items) < 2:
            continue
        rows = [item.row for item in cluster.items]
        for row in rows:
            members[row] = rows
    order: list[int] = []
    placed: set[int] = set()
    for row in group.data_rows:
        if row in placed:
            continue
        for member in members.get(row, [row]):
            if member not in placed:
                order.append(member)
                placed.add(member)
    return order


def move_rows(sheet, group: Group, clusters: list[Cluster]) -> dict[int, int]:
    """Шаг 4.5: ставит пары пересорта друг под другом.

    Блок в рамке должен быть целым, поэтому строки одной грозди
    переставляются к самой верхней строке грозди.
    Возвращает соответствие «старая строка → новая строка».
    """
    targets = list(group.data_rows)
    order = cluster_order(group, clusters)
    if order == targets:
        return {row: row for row in targets}
    snapshots = {row: _row_snapshot(sheet, row) for row in order}
    moved: dict[int, int] = {}
    for target, source in zip(targets, order):
        for column, (value, saved) in enumerate(snapshots[source], start=1):
            cell = sheet.cell(row=target, column=column)
            cell.value = value
            style.apply_style(cell, saved)
        moved[source] = target
    return moved


def process_group(
    sheet,
    group: Group,
    settings,
    decisions: dict[str, bool] | None = None,
    verifier=None,
    skip_rows: set[int] | None = None,
) -> GroupResult:
    """Шаг 4: грозди брендов и разбор расхождений.

    `skip_rows` — строки, которые не участвуют в подборе пересортов.
    Так перезачёты и позиции «не-» выводятся из игры: их пара либо
    прикрепляется к другому товару, либо остаётся без пары.
    """
    blocked = set(skip_rows or ())
    items = []
    for row in group.data_rows:
        if row in blocked:
            continue
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
        decisions,
        strict_brand_only=bool(getattr(settings, "strict_resort", False)),
        price_gate_low=settings.price_gate_low,
        price_gate_high=settings.price_gate_high,
        match_min_score=settings.match_min_score,
        doubtful_limit=settings.doubtful_limit,
        verifier=verifier,
    )

    # Строки одной грозди ставятся рядом до окраски и рамки.
    moved = move_rows(sheet, group, clusters)
    for cluster in clusters:
        for item in cluster.items:
            item.row = moved.get(item.row, item.row)
        cluster.items.sort(key=lambda item: item.row)
    for pair in doubtful:
        pair.first_row = moved.get(pair.first_row, pair.first_row)
        pair.second_row = moved.get(pair.second_row, pair.second_row)

    result = GroupResult(name=group.name, clusters=clusters, doubtful=doubtful, moved=moved)
    for cluster in clusters:
        if len(cluster.items) == 1:
            result.single_rows += 1
        if cluster.decision == DECISION_RESORT:
            result.resort_pieces += sum(abs(item.diff) for item in cluster.items) / 2
            style.frame_block(sheet, [item.row for item in cluster.items], COL_DIFF)
            if cluster.total_sum < 0:
                # Сумма цен пары отрицательная: деньги теряются, суммы остаются.
                for item in cluster.items:
                    style.paint(sheet.cell(row=item.row, column=COL_DIFF), style.YELLOW)
                    style.paint(sheet.cell(row=item.row, column=COL_SUM_DIFF), style.YELLOW)
            else:
                for item in cluster.items:
                    _zero_sum(sheet, item, result)
        elif cluster.decision == DECISION_SURPLUS:
            for item in cluster.items:
                style.paint(sheet.cell(row=item.row, column=COL_DIFF), style.ORANGE)
                sheet.cell(row=item.row, column=COL_TRAIT).value = MARK_NOT_PLUS
                _zero_sum(sheet, item, result)
        elif cluster.decision == DECISION_SHORTAGE:
            for item in cluster.items:
                style.paint(sheet.cell(row=item.row, column=COL_DIFF), style.YELLOW)
                style.paint(sheet.cell(row=item.row, column=COL_SUM_DIFF), style.YELLOW)

    result.remainder_sum = group_remainder(sheet, group)
    return result


def group_remainder(sheet, group: Group) -> float:
    """Сумма остатка группы по столбцу J."""
    return sum(
        _number(sheet.cell(row=row, column=COL_SUM_DIFF).value) for row in group.data_rows
    )


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


def _paint_row(sheet, row: int, color: str) -> None:
    """Красит всю строку позиции: столбцы A–L."""
    for column in range(1, SCAN_COLUMNS + 1):
        style.paint(sheet.cell(row=row, column=column), color)


def plan_comparison(sheet, group: Group, previous, comparison: Comparison) -> list[dict]:
    """Решения сравнения для группы — без записи в файл.

    Правила:

    * в прошлый раз позиция считалась в минус (сумма не обнулена), сейчас
      выходит в плюс → пометка «перез»;
    * в прошлый раз позиция была «не+», сейчас в минусе → пометка «не-».

    Позиции сопоставляются по полному названию товара. Решения берутся до
    разбора пересортов, чтобы помеченные строки в нём не участвовали.
    """
    entries: list[dict] = []
    for row in group.data_rows:
        raw = sheet.cell(row=row, column=COL_NAME).value
        key = prev_book.name_key(raw)
        item = previous.items.get(key) if key else None
        if item is None:
            continue
        comparison.matched += 1
        diff = _number(sheet.cell(row=row, column=COL_DIFF).value)
        if diff > 0 and item.was_minus:
            mark = MARK_CREDIT
            comparison.credit_sum += abs(item.sum_diff)
        elif diff < 0 and item.was_not_plus:
            mark = MARK_NOT_MINUS
        else:
            continue
        entries.append(
            {
                "group": group.name,
                "row": row,
                "name": str(raw or "").strip(),
                "diff": diff,
                "sum_before": round(_number(sheet.cell(row=row, column=COL_SUM_DIFF).value), 2),
                "prev_diff": item.diff,
                "prev_sum": round(item.sum_diff, 2),
                "mark": mark,
            }
        )
    return entries


def apply_comparison(
    sheet,
    entries: list[dict],
    moved: dict[int, int] | None,
    comparison: Comparison,
) -> None:
    """Запись пометок сравнения после перестановки строк."""
    shift = moved or {}
    for entry in entries:
        row = shift.get(entry["row"], entry["row"])
        entry["row"] = row
        sum_cell = sheet.cell(row=row, column=COL_SUM_DIFF)
        unmerge_cell(sheet, row, COL_TRAIT)
        sheet.cell(row=row, column=COL_TRAIT).value = entry["mark"]
        # По обоим правилам разница суммы обнуляется.
        sum_cell.value = 0
        _paint_row(sheet, row, style.GREEN)
        if entry["mark"] == MARK_CREDIT:
            comparison.credits.append(entry)
        else:
            comparison.not_minus.append(entry)


def compare_group(sheet, group: Group, previous, comparison: Comparison) -> list[dict]:
    """Сравнение группы с предыдущей инвентаризацией одним шагом."""
    entries = plan_comparison(sheet, group, previous, comparison)
    apply_comparison(sheet, entries, None, comparison)
    return entries


def credit_shares(previous, credit_sum: float) -> tuple[list[tuple[str, float]], list[str]]:
    """Доли перезачёта по продавцам прошлой сверки.

    Сумма делится так же, как эти позиции списывались в прошлый раз:
    по весам продавцов из предыдущей сверки.
    """
    notes: list[str] = []
    if credit_sum <= 0:
        return [], notes

    sellers = list(getattr(previous, "sellers", ()) or ())
    if not sellers:
        notes.append(
            "Блок продавцов в предыдущей сверке не найден: сумма перезачёта "
            "показана одной строкой, фамилии впишите вручную."
        )
        return [("", round(credit_sum, 2))], notes

    weights = [seller.weight for seller in sellers]
    total = sum(weights)
    if total <= 0:
        notes.append(
            "Веса продавцов в предыдущей сверке пустые: перезачёт разделён поровну."
        )
        weights = [1.0] * len(sellers)
        total = float(len(sellers))
    return (
        [
            (seller.name, round(credit_sum * weight / total, 2))
            for seller, weight in zip(sellers, weights)
        ],
        notes,
    )


def write_group_total(sheet, group: Group) -> str:
    """Шаг 5: формула итога группы."""
    for column in TOTAL_ROW_CLEAR_COLUMNS:
        sheet.cell(row=group.total_row, column=column).value = None
    last = max(group.first_data_row, group.total_row - 1)
    letter = get_column_letter(COL_SUM_DIFF)
    cell = sheet.cell(row=group.total_row, column=COL_SUM_DIFF)
    cell.value = f"=SUM({letter}{group.first_data_row}:{letter}{last})"
    style.style_total_cell(cell)
    return f"{letter}{group.total_row}"


def unmerge_cell(sheet, row: int, column: int) -> None:
    """Снимает объединение, если ячейка входит в него."""
    for merged in [str(item) for item in sheet.merged_cells.ranges]:
        min_col, min_row, max_col, max_row = range_boundaries(merged)
        if min_row <= row <= max_row and min_col <= column <= max_col:
            sheet.unmerge_cells(merged)


def write_grand_total(sheet, total_cells: list[str], row: int) -> None:
    """Шаг 6: общий итог в столбце I строки со словом «Склад:»."""
    unmerge_cell(sheet, row, COL_GRAND_TOTAL)
    cell = sheet.cell(row=row, column=COL_GRAND_TOTAL)
    cell.value = f"=SUM({','.join(total_cells)})"
    style.style_grand_total_cell(cell)


def write_extra_total(sheet, row: int) -> None:
    """Шаг 6.5: ячейка неучтёнки под общим итогом.

    Значение остаётся пустым: сумму неучтёнки вписывают руками.
    Оформление — жёлтая заливка и средняя рамка, как в образце.
    """
    unmerge_cell(sheet, row, COL_GRAND_TOTAL)
    cell = sheet.cell(row=row, column=COL_GRAND_TOTAL)
    if cell.value in (None, ""):
        cell.value = None
    style.style_extra_cell(cell)


def format_workbook(
    sheet,
    warehouse: str,
    settings,
    decisions: dict[str, bool] | None = None,
    sheet_meta: SheetMeta | None = None,
    previous=None,
) -> FormatResult:
    """Полный проход шагов 2–10.

    `previous` — разобранная предыдущая инвентаризация (модуль `prev`).
    Если она передана, сначала берутся решения сравнения, потом идёт
    подбор пересортов без помеченных строк, а под блоком продавцов
    появляется мини-таблица «Перезачёт».
    """
    document = parse(sheet)
    document = shift_header(sheet, document)
    header_row = fill_header(sheet, document, warehouse)

    # Второй слой готовится один раз на файл, а не на каждую группу.
    verifier = load_verifier(settings)

    result = FormatResult(
        document=document,
        warehouse=warehouse,
        verify_used=verifier is not None,
        comparison=Comparison(
            file_name=str(getattr(previous, "file_name", "") or ""),
            date=str(getattr(previous, "date", "") or ""),
        ),
    )
    total_cells: list[str] = []
    for group in document.groups:
        # Сначала решения сравнения: перезачёты и «не-» не идут в пересорт.
        entries: list[dict] = []
        if previous is not None:
            entries = plan_comparison(sheet, group, previous, result.comparison)
        skip_rows = {entry["row"] for entry in entries}

        group_result = process_group(sheet, group, settings, decisions, verifier, skip_rows)
        if entries:
            apply_comparison(sheet, entries, group_result.moved, result.comparison)
            # После обнуления сумм помеченных строк остаток меняется.
            group_result.remainder_sum = group_remainder(sheet, group)
        highlight_rest(sheet, group)
        group_result.total_cell = write_group_total(sheet, group)
        total_cells.append(group_result.total_cell)
        result.groups.append(group_result)

    if total_cells:
        write_grand_total(sheet, total_cells, header_row)
    # Ячейка неучтёнки нужна всегда, даже если итогов групп нет.
    write_extra_total(sheet, header_row + EXTRA_ROW_OFFSET)

    if previous is not None:
        shares, notes = credit_shares(previous, result.comparison.credit_sum)
        result.comparison.shares = shares
        result.comparison.notes = notes
        if result.comparison.marked_rows:
            result.comparison.notes.append(EXCLUDED_NOTE)

    result.last_row = max(group.total_row for group in document.groups)
    # Шаг 10: ручные поля сверки пишутся до геометрии,
    # чтобы высоты строк захватили и блок продавцов.
    result.bottom_row = apply_meta(
        sheet,
        document,
        sheet_meta,
        result.last_row,
        result.comparison.shares,
    )
    style.apply_geometry(sheet, result.last_row)
    return result


def output_filename(warehouse: str, doc_date: str | None) -> str:
    """Шаг 9: имя выходного файла."""
    safe_name = (warehouse or "Склад").strip() or "Склад"
    date_text = doc_date or "без даты"
    return f"{safe_name} {date_text}.xlsx"
