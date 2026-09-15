"""Регрессия записи справочников в объединённые ячейки Excel."""

from __future__ import annotations

import openpyxl

from app.core import refs


def test_write_cells_uses_merged_range_anchor():
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.merge_cells("L4:N4")

    info = refs.RefsInfo(auditors=("Иванов Иван Иванович",))
    written = refs.write_cells(sheet, info, {"auditors": "M4"})

    assert written == ["M4"]
    assert sheet["L4"].value == "Иванов Иван Иванович"
    assert sheet["M4"].value is None
