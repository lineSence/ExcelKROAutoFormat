from __future__ import annotations
import logging
from pathlib import Path
from ..core.learning import append_samples,samples_from_manual
logger=logging.getLogger("excelkro")
def store_answers(old,answers:dict[str,bool],settings)->int:
    if not answers:return 0
    try:
        samples=samples_from_manual(old,answers); return append_samples(Path(settings.train_store_path),samples) if samples else 0
    except Exception:logger.exception("Примеры не сохранены"); return 0
def read_samples(source:Path,name:str,settings)->list:
    from ..core.learning import samples_from_workbook
    return samples_from_workbook(source,name,settings)
