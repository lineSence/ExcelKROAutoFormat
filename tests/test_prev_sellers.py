"""Блок продавцов предыдущей сверки: только ФИО, без товаров и перезачёта.

Данные синтетические и обезличенные: реальные выгрузки в репозиторий
не кладём.
"""

from openpyxl import Workbook

from app.core import prev


def _sheet(last_group_total=True):
    """Лист в том же виде, в каком его отдаёт готовая сверка."""
    book = Workbook()
    sheet = book.active
    sheet.title = "TDSheet"
    sheet["A1"] = "Инвентаризация товаров на складе по группам"
    sheet["B1"] = "от 21 августа 2026 г."

    sheet["A3"] = "№"
    sheet["B3"] = "Сигареты"
    sheet["C3"] = "Хар-ка"
    sheet["A4"] = 1958
    sheet["B4"] = "Сигареты Бостон Россо"
    sheet["D4"] = 5
    sheet["E4"] = 4
    sheet["F4"] = -1
    sheet["G4"] = 190
    sheet["J4"] = -190
    sheet["J5"] = -190

    sheet["A7"] = "№"
    sheet["B7"] = "Электронка"
    sheet["C7"] = "Хар-ка"
    sheet["A8"] = 4374
    sheet["B8"] = "Электронное устройство ВАКА БЛАСТ"
    sheet["D8"] = 1
    sheet["F8"] = -1
    sheet["G8"] = 2460
    sheet["J8"] = 0
    if last_group_total:
        sheet["J9"] = 0

    sheet["B11"] = "ФИО 1"
    sheet["C11"] = -4.2
    sheet["J11"] = 0
    sheet["B12"] = 36
    sheet["B13"] = "ФИО 2"
    sheet["B14"] = 111
    sheet["B15"] = 147

    sheet["B17"] = "Перезачёт"
    sheet["C17"] = 195
    sheet["B18"] = "ФИО 1"
    sheet["B19"] = 144
    return sheet


def test_sellers_are_names_with_weights():
    sheet = _sheet()

    sellers = prev.read_sellers(sheet, 9)

    assert [(item.name, item.weight) for item in sellers] == [
        ("ФИО 1", 36.0),
        ("ФИО 2", 111.0),
    ]


def test_items_of_the_last_group_are_not_sellers():
    """У нижней группы может не быть строки итога — товары всё равно не ФИО."""
    sheet = _sheet(last_group_total=False)

    names = [item.name for item in prev.read_sellers(sheet, 6)]

    assert names == ["ФИО 1", "ФИО 2"]
    assert "Электронка" not in names
    assert not any(name.startswith("Электронное устройство") for name in names)


def test_resort_table_does_not_double_sellers():
    """ФИО из мини-таблицы «Перезачёт» второй раз в список не попадает."""
    sheet = _sheet()

    names = [item.name for item in prev.read_sellers(sheet, 9)]

    assert names.count("ФИО 1") == 1
    assert "Перезачёт" not in names


def test_total_weight_counts_only_sellers():
    sheet = _sheet()

    sellers = prev.read_sellers(sheet, 9)
    sheet_data = prev.PrevSheet(sellers=sellers)

    assert sheet_data.total_weight == 147.0
