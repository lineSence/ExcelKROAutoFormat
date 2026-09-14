"""Отметка «есть фото» в готовом файле сверки.

Когда человек подтвердил, что на снимке именно эта позиция, в столбец 12
(`L`) этой строки ставится пометка. Если в ячейке уже что-то есть, пометка
дописывается через запятую, а повторное подтверждение ничего не дублирует.

Модуль правит уже собранный файл, поэтому ошибки не поднимаются наверх:
возвращается пара «получилось, пояснение». Ответ человека важнее отметки:
если файл не открылся, пример всё равно запишется.

Пометка живёт в том файле, который скачивается со страницы результата.
Пересборка сверки (подтверждение спорных пар, ручные поля, заявки реестра)
собирает файл заново из исходника, поэтому фото подтверждают последним шагом.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

logger = logging.getLogger("excelkro.photo_mark")

# Столбец 12 — `L`, служебный комментарий сверки.
COLUMN = 12
NOTE = "есть фото"


def _sheet(workbook, sheet_name: str):
    if sheet_name and sheet_name in workbook.sheetnames:
        return workbook[sheet_name]
    return workbook[workbook.sheetnames[0]]


def mark(
    path: str | Path,
    row: int,
    sheet_name: str = "",
    note: str = NOTE,
    column: int = COLUMN,
) -> tuple[bool, str]:
    """Ставит пометку в столбец комментария указанной строки."""
    file = Path(path)
    number = int(row or 0)
    if number <= 0:
        return False, "Номер строки не указан: отметка не поставлена."
    if not file.is_file():
        return False, "Файл сверки уже удалён: отметка не поставлена."

    try:
        workbook = openpyxl.load_workbook(file)
    except Exception as error:  # noqa: BLE001
        logger.warning("Файл сверки не открыт: %s", error)
        return False, f"Файл сверки не открылся: {error}"

    try:
        sheet = _sheet(workbook, sheet_name)
        cell = sheet.cell(row=number, column=column)
        parts = [part.strip() for part in str(cell.value or "").split(",")]
        parts = [part for part in parts if part]
        if any(part.casefold() == note.casefold() for part in parts):
            return True, f"В строке {number} пометка «{note}» уже стояла."
        parts.append(note)
        cell.value = ", ".join(parts)
        workbook.save(file)
    except Exception as error:  # noqa: BLE001
        logger.exception("Отметка о фото не записана")
        return False, f"Отметка не записана: {type(error).__name__}: {error}"
    finally:
        workbook.close()

    return True, f"В строку {number} вписана пометка «{note}»."
