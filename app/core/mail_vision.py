"""Связка писем ревизоров со зрением и сверкой.

Раньше скачанные из письма снимки жили сами по себе: человек сохранял их
по ссылке и загружал на `/vision` руками, а архив письма собирался с
повторным обращением к модели. Теперь разбор идёт один раз — при
обработке сверки:

1. Кандидатами становятся излишки уже разобранной сверки (`F > 0`).
2. Снимки писем последнего захода уходят в `vision.recognize_all`
   пачкой: один токен доступа и пауза между снимками на всю пачку.
3. Каждому снимку письма ставится имя узнанного товара (`Photo.title`),
   поэтому архив письма (`mail.build_archive`) сразу выходит обработанным.
4. Ответы модели возвращаются наружу, чтобы веб-слой положил их в `PHOTOS`
   по токену сверки: человек подтверждает строки на странице «Фото
   товара», как и раньше.

Модуль ничего не решает за человека и не пишет в файл сверки. Ошибок
наружу не отдаёт: нет ключа, выключено распознавание, нет сети — всё это
возвращается текстом в отчёте, иначе страница готовой сверки падала бы
с Internal server error.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import vision as vision_core

logger = logging.getLogger("excelkro.mail_vision")

NO_KEY = (
    "Разбор снимков писем не запускался: распознавание выключено или не введён "
    "ключ GigaChat. В архив письма файлы попадут со своими именами."
)
NO_ITEMS = "В сверке нет излишков: снимки из писем сравнивать не с чем."
NO_PHOTOS = "В письмах последнего захода снимков нет."
FAILED = "Часть снимков писем не разобрана. Подробности — в журнале службы."


def letter_photos(letter) -> list[Path]:
    """Снимки письма, которые действительно лежат на диске.

    После захода в ящик данные вложений из памяти убираются, остаётся путь;
    файл мог уже уехать по сроку хранения (`mail_keep_days`).
    """
    paths: list[Path] = []
    for item in getattr(letter, "photos", None) or []:
        path = str(getattr(item, "path", "") or "")
        if not path:
            continue
        file = Path(path)
        if file.is_file():
            paths.append(file)
    return paths


def named(letter) -> int:
    """Сколько снимков письма уже получили имя от нейросети."""
    return sum(
        1
        for item in getattr(letter, "photos", None) or []
        if str(getattr(item, "title", "") or "").strip()
    )


def recognize_letters(letters, items, settings, recognizer=None) -> dict:
    """Разбирает снимки писем и даёт им имя узнанного товара.

    `items` — позиции сверки (`vision.items_from_rows`), кандидатами внутри
    станут только излишки. `recognizer` подменяется в тестах, чтобы обойтись
    без сети.

    Возвращает отчёт для страницы: сколько писем и снимков разобрано,
    сколько снимков получили имя, ответы модели (`results`) и текст о том,
    почему разбора не было.
    """
    report: dict = {
        "letters": 0,
        "photos": 0,
        "named": 0,
        "results": [],
        "note": "",
        "error": "",
    }
    run = recognizer or vision_core.recognize_all
    config = vision_core.load_config(settings)
    if not config.get("vision_enabled") or not config.get("vision_api_key"):
        report["note"] = NO_KEY
        return report
    if not items:
        report["note"] = NO_ITEMS
        return report

    for letter in letters or []:
        paths = letter_photos(letter)
        if not paths:
            continue
        report["letters"] += 1
        report["photos"] += len(paths)
        try:
            found = list(run(paths, items, settings))
        except Exception:  # noqa: BLE001
            logger.exception(
                "Снимки письма %s не разобраны", getattr(letter, "uid", "")
            )
            report["error"] = FAILED
            continue
        by_name = {str(getattr(answer, "photo", "")): answer for answer in found}
        for item in getattr(letter, "photos", None) or []:
            path = str(getattr(item, "path", "") or "")
            answer = by_name.get(Path(path).name) if path else None
            best = getattr(answer, "best", None) if answer is not None else None
            if best is not None and best.name:
                item.title = best.name
                report["named"] += 1
        report["results"].extend(
            answer for answer in found if getattr(answer, "candidates", None)
        )

    if not report["photos"]:
        report["note"] = NO_PHOTOS
    return report


def summary(report: dict) -> str:
    """Короткая строка о разборе снимков писем для страницы сверки."""
    if not report:
        return ""
    note = str(report.get("note") or "")
    if note:
        return note
    if not report.get("photos"):
        return ""
    text = (
        f"Снимки из писем разобраны: {report.get('photos') or 0}, "
        f"узнано товаров: {report.get('named') or 0}. "
        "Архив письма скачивается кнопкой выше, подтверждение строк — "
        "в разделе «Фото товара»."
    )
    error = str(report.get("error") or "")
    return f"{text} {error}".strip()
