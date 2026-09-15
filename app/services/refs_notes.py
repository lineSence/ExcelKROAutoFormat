from __future__ import annotations
import logging
from ..core import refs_sync
logger=logging.getLogger("excelkro")
def note_refs(result,settings)->None:
    try:refs_sync.note_fill(settings.refs_state_path,result.refs)
    except Exception:logger.exception("Журнал справочников не обновлён")
