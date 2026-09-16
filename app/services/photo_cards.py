"""Карточки снимков архива и ответы человека «Да»/«Нет».

Раньше подтверждение шло только таблицей кандидатов: человек искал строку
в списке и на каждый снимок получал перезагрузку страницы. Теперь на
странице «Фото товара» сразу видна сетка карточек — все снимки из архива
сверки, — и решение принимается двумя кнопками:

* «Да» — подтверждается строка, которую предложила модель. Номер уходит в
  `job.photo_rows`, пометка «нет фото» с этой строки снимается
  (`photos.photo_notes`), а снимок в архиве письма получает имя узнанного
  товара (`Photo.title` — отсюда берётся имя файла в архиве).
* «Нет» — имя снимка в архиве стирается, пометка «нет фото» на строке
  остаётся, а снимок уходит в ручной подбор: он остаётся в `job.photos` и
  показывается таблицей кандидатов ниже.

Ответ пишется в базу примеров (`vision.remember`) в обоих случаях: по ней
считается точность на своих фото.

Модуль ничего не решает за человека и не падает наружу: не нашёлся снимок
или модель не предложила строку — возвращается текст для страницы.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core import mail_match
from ..core import vision as vision_core
from .photos import drop_photo, photo_notes

logger = logging.getLogger("excelkro")

YES = "yes"
NO = "no"
MANUAL = "manual"

NO_CARD = "Снимок не найден: архив письма мог уехать по сроку хранения."
NO_ROW = "Модель строку не предложила: нажмите «Нет» и подберите строку руками."
NO_DECISION = "Ответ не понят: нужно «Да» или «Нет»."


def card_key(uid, name) -> str:
    """Ключ карточки: письмо и имя файла снимка."""
    return f"{uid}/{name}"


def _letters_for(job, store) -> list:
    warehouse = str((job.result.summary or {}).get("warehouse") or "")
    return list(mail_match.split(warehouse, store.items).get("mine") or [])


def _answer_for(job, name):
    """Ответ модели по снимку с таким именем файла."""
    for item in job.photos:
        if str(getattr(item, "photo", "") or "") == str(name):
            return item
    return None


def _best(answer):
    if answer is None:
        return None
    best = getattr(answer, "best", None)
    if best is not None:
        return best
    candidates = list(getattr(answer, "candidates", None) or [])
    return candidates[0] if candidates else None


def _photo_files(letter) -> list:
    files = []
    for item in getattr(letter, "photos", None) or []:
        path = str(getattr(item, "path", "") or "")
        if path and Path(path).is_file():
            files.append((item, Path(path).name))
    return files


def cards(job, store) -> list[dict]:
    """Все снимки архива сверки карточками для страницы «Фото товара»."""
    out: list[dict] = []
    for letter in _letters_for(job, store):
        uid = str(getattr(letter, "uid", "") or "")
        for item, name in _photo_files(letter):
            answer = _answer_for(job, name)
            best = _best(answer)
            key = card_key(uid, name)
            out.append(
                {
                    "key": key,
                    "uid": uid,
                    "name": name,
                    "url": f"/mail/photo/{uid}/{name}",
                    "title": str(getattr(item, "title", "") or ""),
                    "text": str(getattr(answer, "text", "") or ""),
                    "digest": str(getattr(answer, "digest", "") or ""),
                    "row": int(getattr(best, "row", 0) or 0),
                    "guess": str(getattr(best, "name", "") or ""),
                    "source": str(getattr(best, "source", "") or ""),
                    "state": str(job.photo_answers.get(key) or ""),
                }
            )
    return out


def manual_items(job, store) -> list:
    """Что показывать таблицей кандидатов: ручной подбор и снимки с диска.

    Снимки архива, по которым ответа ещё нет, второй раз списком не идут:
    они уже видны карточками. Остаются отправленные в ручной подбор и
    снимки, загруженные руками на этой же странице.
    """
    known = {}
    for card in cards(job, store):
        known[card["name"]] = card["state"]
    keep = []
    for item in job.photos:
        name = str(getattr(item, "photo", "") or "")
        if name in known and known[name] != MANUAL:
            continue
        keep.append(item)
    return keep


def _set_title(store, uid: str, name: str, title: str) -> None:
    """Имя узнанного товара для файла в архиве письма."""
    letter = store.by_uid(uid) if hasattr(store, "by_uid") else None
    if letter is None:
        return
    changed = False
    for item, file_name in _photo_files(letter):
        if file_name != name:
            continue
        if str(getattr(item, "title", "") or "") != title:
            item.title = title
            changed = True
    if not changed:
        return
    try:
        store.save()
    except Exception:  # noqa: BLE001
        logger.exception("Имя снимка в архиве не сохранено")


def apply_answer(job, store, settings, key: str, decision: str) -> tuple[bool, str, str]:
    """Записывает ответ по карточке. Возвращает (получилось, состояние, текст)."""
    choice = str(decision or "").strip().lower()
    if choice not in (YES, NO):
        return False, "", NO_DECISION
    card = next((item for item in cards(job, store) if item["key"] == str(key or "")), None)
    if card is None:
        return False, "", NO_CARD
    yes = choice == YES
    if yes and card["row"] <= 0:
        return False, "", NO_ROW

    try:
        vision_core.remember(
            settings,
            card["name"],
            card["digest"],
            card["text"],
            card["row"] if yes else 0,
            card["guess"],
            yes,
            card["source"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Ответ по снимку не сохранён в базу примеров")

    if yes:
        job.photo_rows.add(card["row"])
        job.photo_answers[card["key"]] = YES
        drop_photo(job, card["name"], card["digest"])
        _set_title(store, card["uid"], card["name"], card["guess"])
    else:
        if card["row"] > 0:
            job.photo_rows.discard(card["row"])
        job.photo_answers[card["key"]] = MANUAL
        _set_title(store, card["uid"], card["name"], "")

    ok, detail = photo_notes(job, settings)
    if not ok:
        return False, "", detail
    if yes:
        text = f"Строка {card['row']} подтверждена: {card['guess']}."
    else:
        text = "Снимок ушёл в ручной подбор строки — раздел «Что распознано» ниже."
    return True, YES if yes else MANUAL, f"{text} {detail}".strip()
