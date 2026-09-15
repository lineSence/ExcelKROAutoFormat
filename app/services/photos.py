from __future__ import annotations
import logging
from ..core import photo_mark, vision as vision_core
logger=logging.getLogger("excelkro")
def vision_items(result,settings)->list[dict]:return vision_core.items_from_rows(result,settings)
def surplus_count(result,settings)->int:return len(vision_core.surplus_items(result,settings))
def photo_notes(job,settings)->tuple[bool,str]:
    rows=sorted(job.photo_rows)
    if not rows:return True,""
    try:changed=photo_mark.apply(job.result.output_path,rows,sheet_name=settings.sheet_name)
    except Exception as error:
        logger.exception("Пометки по фото не обновлены"); return False,f"Файл не обновлён: {error}"
    return True,f"Строк с фото: {changed}."
def keep_answers(job,report:dict)->None:
    for row in report.get("rows",[]) or []:
        n=int(row.get("row") or 0)
        if n>0:job.photo_rows.add(n)
def drop_photo(job,photo:str,digest:str)->None:
    job.photos[:]=[x for x in job.photos if not(str(x.get("photo",""))==str(photo) and str(x.get("digest",""))==str(digest))]
