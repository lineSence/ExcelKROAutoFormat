"""Карточки снимков архива и ответы человека по ним.

Раньше подтверждение шло только таблицей кандидатов: человек искал строку
в списке и на каждый снимок получал перезагрузку страницы. Теперь на
странице «Фото товара» сразу видна сетка карточек — все снимки из архива
сверки, — и решение принимается на самой карточке:

* «Да» — подтверждается строка, которую предложила модель. Номер уходит в
  `job.photo_rows`, пометка «нет фото» с этой строки снимается
  (`photos.photo_notes`), а снимок в архиве письма получает имя узнанного
  товара (`Photo.title` — отсюда берётся имя файла в архиве).
* «Нет» — открывается выпадающий список плюсующих товаров сверки
  (правка владельца 16.09.2026). Человек выбирает строку руками: она
  подтверждается так же, как по кнопке «Да», а имя файла в архиве
  становится именем выбранного товара.
* «Не товар» — первый пункт того же списка. Строка не подтверждается,
  пометка «нет фото» остаётся, а **имя снимка в архиве не меняется:
  остаётся ровно таким, каким пришло в письме**. Для этого `Photo.title`
  не трогается вовсе: `mail_archive.entry_name` без него берёт
  собственное имя вложения.

Ответ пишется в базу примеров (`vision.remember`) в любом случае: по ней
считается точность на своих фото.

Модуль ничего не решает за человека и не падает наружу: не нашёлся снимок
или строка выбрана не из списка — возвращается текст для страницы.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core import mail_match
from ..core import vision as vision_core
from .photos import drop_photo, photo_notes

logger = logging.getLogger("excelkro")

# Ответы, которые принимает маршрут.
YES = "yes"
PICK = "pick"

# Состояния карточки после ответа.
DONE = "yes"
NOT_ITEM = "notitem"

# Значение пункта «Не товар» в выпадающем списке.
NOT_ITEM_ROW = 0
NOT_ITEM_LABEL = "Не товар"

NO_CARD = "Снимок не найден: архив письма мог уехать по сроку хранения."
NO_ROW = "Модель строку не предложила: нажмите «Нет» и выберите товар из списка."
NO_PICK = "Такой строки в сверке нет: выберите товар из списка или «Не товар»."
NO_DECISION = "Ответ не понят: нужно «Да» или выбор из списка."


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


def options(job, settings) -> list[dict]:
    """Плюсующие товары сверки для выпадающего списка ручного выбора.

    Тот же набор, что уходит модели: строки с излишком (`F > 0`), крупные
    первыми. Минусующие строки в список не попадают — фото товара
    объясняет излишек, а не недостачу.
    """
    rows = getattr(job.result, "clusters", None) or []
    items = vision_core.items_from_rows(rows, tuple(getattr(settings, "type_words", ())))
    return [
        {"row": item.row, "name": item.name, "diff": item.diff}
        for item in vision_core.surplus_items(items)
    ]


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
    """Что показывать таблицей кандидатов ниже сетки.

    Снимки архива идут только карточками: выбор строки делается там же,
    в выпадающем списке. Таблица остаётся для фото, загруженных руками на
    этой странице, — их в архиве письма нет.
    """
    known = {card["name"] for card in cards(job, store)}
    return [item for item in job.photos if str(getattr(item, "photo", "") or "") not in known]


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


def _chosen(job, settings, card: dict, row: int) -> tuple[int, str, str]:
    """Разбирает выбор человека: (строка, товар, ошибка)."""
    if row <= NOT_ITEM_ROW:
        return NOT_ITEM_ROW, "", ""
    for option in options(job, settings):
        if int(option["row"]) == row:
            return row, str(option["name"]), ""
    # Строка модели могла остаться в списке карточки после пересборки файла.
    if row == card["row"] and card["guess"]:
        return row, card["guess"], ""
    return 0, "", NO_PICK


def apply_answer(
    job,
    store,
    settings,
    key: str,
    decision: str,
    row: int = 0,
) -> tuple[bool, str, str]:
    """Записывает ответ по карточке. Возвращает (получилось, состояние, текст).

    `decision`:

    * `yes` — согласие со строкой модели;
    * `pick` — ручной выбор из списка: `row` больше нуля, либо ноль,
      если выбран пункт «Не товар».
    """
    choice = str(decision or "").strip().lower()
    if choice not in (YES, PICK):
        return False, "", NO_DECISION
    card = next((item for item in cards(job, store) if item["key"] == str(key or "")), None)
    if card is None:
        return False, "", NO_CARD

    if choice == YES:
        if card["row"] <= 0:
            return False, "", NO_ROW
        number, name, source = card["row"], card["guess"], card["source"] or "модель"
    else:
        number, name, trouble = _chosen(job, settings, card, int(row or 0))
        if trouble:
            return False, "", trouble
        source = "рука"

    picked = number > 0
    try:
        vision_core.remember(
            settings,
            card["name"],
            card["digest"],
            card["text"],
            number,
            name,
            picked,
            source,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Ответ по снимку не сохранён в базу примеров")

    if picked:
        job.photo_rows.add(number)
        job.photo_answers[card["key"]] = DONE
        _set_title(store, card["uid"], card["name"], name)
    else:
        # «Не товар»: строка модели не подтверждается, а имя файла в архиве
        # остаётся тем, что пришло в письме, — `title` не трогаем.
        if card["row"] > 0:
            job.photo_rows.discard(card["row"])
        job.photo_answers[card["key"]] = NOT_ITEM
    drop_photo(job, card["name"], card["digest"])

    ok, detail = photo_notes(job, settings)
    if not ok:
        return False, "", detail
    if picked:
        text = f"Строка {number} подтверждена: {name}."
    else:
        text = "Отмечено «не товар»: имя снимка в архиве осталось как в письме."
    return True, DONE if picked else NOT_ITEM, f"{text} {detail}".strip()
