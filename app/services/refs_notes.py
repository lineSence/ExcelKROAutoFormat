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


def configure_auto_confirm(enabled: bool) -> None:
    """Установить режим авто-подтверждения для текущей задачи."""
    _AUTO_CONFIRM.set(bool(enabled))


def _pending(info):
    """Либо вернуть распознанные значения целиком, либо оставить старый фильтр."""
    if _AUTO_CONFIRM.get():
        return info
    return _ORIGINAL_PENDING(info)


# Pipeline уже вызывает refs.pending(). Заменяем только точку фильтрации,
# сохраняя старую функцию для режима, когда авто-подтверждение выключено.
refs_core.pending = _pending


def note_refs(result, settings) -> None:
    """Записать итог работы справочников в журнал, не ломая обработку файла."""
    try:
        refs_sync.note_fill(settings.refs_state_path, result.refs)
    except Exception:
        logger.exception("Журнал справочников не обновлён")
