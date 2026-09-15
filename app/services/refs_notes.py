from __future__ import annotations

import logging
from contextvars import ContextVar

from ..core import pipeline as pipeline_core
from ..core import refs as refs_core
from ..core import refs_sync

logger = logging.getLogger("excelkro")

# Настройка берётся на уровне конкретного запроса/фоновой задачи. ContextVar
# позволяет не смешивать её между параллельными сверками: worker получает копию
# контекста при переходе через Starlette/AnyIO run_in_threadpool.
_AUTO_CONFIRM = ContextVar("excelkro_refs_auto_confirm", default=True)
_SKIP_DIRECT_WRITES = ContextVar("excelkro_refs_skip_direct_writes", default=False)

_ORIGINAL_PENDING = refs_core.pending
_ORIGINAL_WRITE_CELLS = refs_core.write_cells
_ORIGINAL_FORMAT_WORKBOOK = pipeline_core.format_workbook
_ORIGINAL_PROCESS = pipeline_core.process


def configure_auto_confirm(enabled: bool) -> None:
    """Установить режим авто-подтверждения для текущей задачи."""
    _AUTO_CONFIRM.set(bool(enabled))


def _pending(info):
    """Либо вернуть распознанные значения целиком, либо оставить старый фильтр."""
    if _AUTO_CONFIRM.get():
        return info
    return _ORIGINAL_PENDING(info)


def _write_cells(sheet, info, cells):
    """Не дублировать справочники в шапке: запись идёт через SheetMeta."""
    if _SKIP_DIRECT_WRITES.get():
        return []
    return _ORIGINAL_WRITE_CELLS(sheet, info, cells)


def _refs_meta(sheet_meta, warehouse: str, document, settings):
    """Добавить справочники в SheetMeta до физической записи Excel."""
    from ..core.meta import SheetMeta
    from ..core.refs_sync import local_paths

    day = pipeline_core.doc_day(document.doc_date)
    planning, schedule = local_paths(settings.refs_dir)
    books = refs_core.load_books(str(planning), str(schedule))
    info = refs_core.lookup(
        warehouse,
        day,
        books,
        checker=settings.default_checker,
        min_score=settings.refs_match_min_score,
        days_around=settings.refs_days_around,
        confirm_min_score=settings.refs_confirm_min_score,
    )
    to_write = info if _AUTO_CONFIRM.get() or not info.uncertain else _ORIGINAL_PENDING(info)

    meta = sheet_meta if sheet_meta is not None else SheetMeta()
    if to_write.reason and not meta.reason:
        meta.reason = to_write.reason
    if to_write.admin and not meta.admin:
        meta.admin = to_write.admin
    if to_write.checker and not meta.checked_by:
        meta.checked_by = to_write.checker
    if to_write.auditors and not meta.auditors:
        meta.auditors = tuple(to_write.auditors)
    return meta


def _format_workbook(*args, **kwargs):
    """Передать распознанные справочники существующему нижнему meta-блоку."""
    if len(args) >= 5:
        sheet, warehouse, settings, _decisions, sheet_meta = args[:5]
        document = pipeline_core.parse(sheet)
        sheet_meta = _refs_meta(sheet_meta, warehouse, document, settings)
        args = (*args[:4], sheet_meta, *args[5:])
    else:
        sheet = kwargs["sheet"]
        warehouse = kwargs.get("warehouse", "")
        settings = kwargs["settings"]
        document = pipeline_core.parse(sheet)
        kwargs["sheet_meta"] = _refs_meta(kwargs.get("sheet_meta"), warehouse, document, settings)
    return _ORIGINAL_FORMAT_WORKBOOK(*args, **kwargs)


def _process(*args, **kwargs):
    """На время pipeline отключить старую прямую запись refs в шапку."""
    token = _SKIP_DIRECT_WRITES.set(True)
    try:
        return _ORIGINAL_PROCESS(*args, **kwargs)
    finally:
        _SKIP_DIRECT_WRITES.reset(token)


refs_core.pending = _pending
refs_core.write_cells = _write_cells
pipeline_core.format_workbook = _format_workbook


def install_process_hook(upload_job_module) -> None:
    """Заменить локальную ссылку `process` в upload_job после его импорта."""
    upload_job_module.process = _process


def note_refs(result, settings) -> None:
    """Записать итог работы справочников в журнал, не ломая обработку файла."""
    try:
        refs_sync.note_fill(settings.refs_state_path, result.refs)
    except Exception:
        logger.exception("Журнал справочников не обновлён")
