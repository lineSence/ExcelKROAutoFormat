from __future__ import annotations

import logging
from collections.abc import Callable

from starlette.concurrency import run_in_threadpool

from ..core import mail_match, mail_vision
from ..state.jobs import Job
from ..state.letters import LetterStore
from ..web.messages import GENERIC_ERROR, NO_PHOTO, OTHER_STORE
from .photos import keep_answers, photo_notes, vision_items

logger = logging.getLogger("excelkro")


def letters_for(result, store: LetterStore) -> dict:
    warehouse = str((result.summary or {}).get("warehouse") or "")
    return mail_match.split(warehouse, store.items)


def mail_note(mine: list) -> dict:
    """Подсказки из писем для страницы готовой сверки."""
    letters_out: list[dict] = []
    in_list = False
    words: list[str] = []
    seller = ""
    for letter in mine:
        hints = getattr(letter, "hints", None) or {}
        if hints.get("in_list"):
            in_list = True
            for word in hints.get("list_words") or []:
                if word not in words:
                    words.append(word)
        if not seller and hints.get("seller"):
            seller = str(hints["seller"])
        letters_out.append(
            {
                "uid": str(getattr(letter, "uid", "")),
                "subject": str(getattr(letter, "subject", "") or "без темы"),
                "store": str(hints.get("store") or ""),
                "in_list": bool(hints.get("in_list")),
                "seller": str(hints.get("seller") or ""),
            }
        )
    return {
        "letters": letters_out,
        "count": len(mine),
        "photos": sum(len(getattr(letter, "photos", None) or []) for letter in mine),
        "in_list": in_list,
        "list_words": words,
        "seller": seller,
    }


def mail_list(store: LetterStore) -> list[dict]:
    rows: list[dict] = []
    for letter in store.items:
        hints = getattr(letter, "hints", None) or {}
        photos = mail_vision.letter_photos(letter)
        rows.append(
            {
                "uid": str(getattr(letter, "uid", "")),
                "subject": str(getattr(letter, "subject", "") or "без темы"),
                "sender": str(getattr(letter, "sender", "") or "неизвестно"),
                "day": str(getattr(letter, "day", "") or ""),
                "store": str(hints.get("store") or ""),
                "photos": len(getattr(letter, "photos", None) or []),
                "on_disk": len(photos),
                "named": mail_vision.named(letter),
            }
        )
    return rows


def name_photos(letter, job: Job, settings) -> list:
    paths = mail_vision.letter_photos(letter)
    return [{"path": str(path)} for path in paths]


async def letter_vision(
    job: Job,
    letter,
    store: LetterStore,
    settings,
    base: Callable,
) -> tuple[bool, str]:
    warehouse = str((job.result.summary or {}).get("warehouse") or "")
    fits, _reason = mail_match.fits(warehouse, letter)
    if not fits:
        return False, OTHER_STORE
    if not (getattr(letter, "photos", None) or []):
        return False, NO_PHOTO
    try:
        report = await run_in_threadpool(
            mail_vision.recognize_letters,
            [letter],
            vision_items(job.result, settings),
            base(),
            False,
            0,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Снимки письма не разобраны")
        return False, GENERIC_ERROR
    job.mail_vision = dict(report)
    keep_answers(job, report)
    ok, detail = photo_notes(job, settings)
    if not ok:
        return False, detail
    return True, f"{mail_vision.summary(report)} {detail}".strip()


async def mail_vision_for(
    job: Job,
    store: LetterStore,
    settings,
    base: Callable,
) -> dict:
    link = letters_for(job.result, store)
    mine = link["mine"]
    if not mine:
        report = {"results": [], "note": link.get("note", ""), "photos": 0, "named": 0}
        job.mail_vision = report
        return report
    try:
        report = await run_in_threadpool(
            mail_vision.recognize_letters,
            mine,
            vision_items(job.result, settings),
            base(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Снимки писем не разобраны")
        return {}
    job.mail_vision = dict(report)
    keep_answers(job, report)
    photo_notes(job, settings)
    return report
