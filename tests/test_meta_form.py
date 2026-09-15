"""Регрессии: повторяющиеся поля формы и адреса справочников в шапке."""

from openpyxl import Workbook
from starlette.datastructures import FormData

from app.core import refs
from app.services import refs_notes
from app.web.routers.result import _meta_form


def test_meta_form_keeps_all_sellers_and_hours():
    form = FormData(
        [
            ("reason", "плановая"),
            ("sellers", "Иванов Иван Иванович"),
            ("seller_hours", "8"),
            ("sellers", "Петров Пётр Петрович"),
            ("seller_hours", "6"),
            ("sellers", "Сидоров Сидор Сидорович"),
            ("seller_hours", "4"),
            ("auditors", "Ревизор Один"),
            ("auditors", "Ревизор Два"),
        ]
    )

    data = _meta_form(form)

    assert data["sellers"] == [
        "Иванов Иван Иванович",
        "Петров Пётр Петрович",
        "Сидоров Сидор Сидорович",
    ]
    assert data["seller_hours"] == ["8", "6", "4"]
    assert data["auditors"] == ["Ревизор Один", "Ревизор Два"]


def test_refs_write_uses_configured_cell_after_merged_header():
    book = Workbook()
    sheet = book.active
    sheet["A3"] = "Склад:"
    sheet.merge_cells("A3:L3")

    info = refs.RefsInfo(admin="Казаков А.", auditors=("Иванов Иван Иванович",))
    refs_notes._write_cells(
        sheet,
        info,
        {"admin": "L3", "auditors": "L5"},
    )

    assert sheet["A3"].value == "Склад:"
    assert sheet["L3"].value == "Казаков А."
    assert sheet["L5"].value == "Иванов Иван Иванович"
