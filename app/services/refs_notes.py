from __future__ import annotations

import logging
from contextvars import ContextVar

from ..core import refs as refs_core
from ..core import refs_sync

logger = logging.getLogger("excelkro")

# Настройка берётся на уровне конкретного запроса/фоновой задачи. ContextVar
# позволяет не смешивать её между параллельными сверками: worker получает копию
# контекста при переходе через Starlette/AnyIO run_in_threadpool.
_AUTO_CONFIRM = ContextVar("excelkro_refs_auto_confirm", default=True)
_ORIGINAL_PENDING = refs_core.pending
_ORIGINAL_WRITE_CELLS = refs_core.write_cells


def configure_auto_confirm(enabled: bool) -> None:
    """Установить режим авто-подтверждения для текущей задачи."""
    _AUTO_CONFIRM.set(bool(enabled))


def _pending(info):
    """Либо вернуть распознанные значения целиком, либо оставить старый фильтр."""
    if _AUTO_CONFIRM.get():
        return info
    return _ORIGINAL_PENDING(info)


def _unmerge_target(sheet, address: str) -> None:
    """Освободить целевой адрес справочника, сохранив якорное значение.

    В шаблонах 1С отдельные части шапки иногда объединены. Если настройка
    справочника указывает, например, `L3`, а диапазон `A3:L3` объединён,
    запись в `L3` невозможна: openpyxl считает её MergedCell. Здесь снимаем
    только конфликтующее объединение. Значение якоря (например, «Склад:» в
    `A3`) остаётся на месте, после чего `refs.write_cells()` пишет именно в
    настроенный адрес `L3`.
    """
    cell = sheet[address]
    for merged_range in list(sheet.merged_cells.ranges):
        if cell.coordinate not in merged_range:
            continue
        if merged_range.start_cell.coordinate == address:
            return
        sheet.unmerge_cells(merged_range.coord)
        logger.debug(
            "Целевой адрес %s был внутри объединения %s; объединение снято",
            address,
            merged_range.coord,
        )
        return


def _write_cells(sheet, info, cells):
    """Записать справочники строго в заданные адреса, учитывая объединения."""
    for address in cells.values():
        if address:
            try:
                _unmerge_target(sheet, address)
            except (KeyError, TypeError):
                continue
    return _ORIGINAL_WRITE_CELLS(sheet, info, cells)


# Pipeline уже вызывает refs.pending() и refs.write_cells(). Заменяем точки
# фильтрации и записи, сохраняя старую логику при выключенном авто-подтверждении.
refs_core.pending = _pending
refs_core.write_cells = _write_cells


def note_refs(result, settings) -> None:
    """Записать итог работы справочников в журнал, не ломая обработку файла."""
    try:
        refs_sync.note_fill(settings.refs_state_path, result.refs)
    except Exception:
        logger.exception("Журнал справочников не обновлён")
