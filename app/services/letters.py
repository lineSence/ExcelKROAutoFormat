from __future__ import annotations
import logging
from collections.abc import Callable
from starlette.concurrency import run_in_threadpool
from ..core import mail_match,mail_vision
from ..state.jobs import Job
from ..state.letters import LetterStore
from ..web.messages import GENERIC_ERROR,NO_PHOTO,OTHER_STORE
from .photos import keep_answers,photo_notes,vision_items
logger=logging.getLogger("excelkro")
def letters_for(result,store:LetterStore)->dict:
    mine,others=mail_match.split(result,store.items); return {"mine":list(mine),"others":list(others),"note":OTHER_STORE if others and not mine else ""}
def mail_note(mine:list[dict])->dict:return {"letters":list(mine),"count":len(mine),"photos":sum(len(x.get("photos") or []) for x in mine)}
def mail_list(store:LetterStore)->list[dict]:return [{"uid":x.get("uid"),"subject":x.get("subject",""),"sender":x.get("sender",""),"date":x.get("date",""),"store":x.get("store",""),"photos":list(x.get("photos") or [])} for x in store.items]
def name_photos(letter:dict,job:Job,settings)->list[dict]:return mail_vision.letter_photos(letter,job.mail_vision,settings)
async def letter_vision(job:Job,letter:dict,store:LetterStore,settings,base:Callable)->tuple[bool,str]:
    if not mail_match.fits(job.result,letter):return False,OTHER_STORE
    if not(letter.get("photos") or []):return False,NO_PHOTO
    try:report=await run_in_threadpool(mail_vision.recognize_letters,[letter],vision_items(job.result,settings),base())
    except Exception:
        logger.exception("Снимки письма не разобраны"); return False,GENERIC_ERROR
    job.mail_vision=dict(report); keep_answers(job,report); ok,detail=photo_notes(job,settings)
    return (False,detail) if not ok else (True,f"{mail_vision.summary(report)} {detail}".strip())
async def mail_vision_for(job:Job,store:LetterStore,settings,base:Callable)->dict:
    mine=letters_for(job.result,store)["mine"]
    if not mine:return {}
    try:report=await run_in_threadpool(mail_vision.recognize_letters,mine,vision_items(job.result,settings),base())
    except Exception:
        logger.exception("Снимки писем не разобраны"); return {}
    job.mail_vision=dict(report); keep_answers(job,report); photo_notes(job,settings); return report
