"""Регрессия: повторяющиеся поля HTML-формы не должны терять строки."""

from starlette.datastructures import FormData, MutableHeaders

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
