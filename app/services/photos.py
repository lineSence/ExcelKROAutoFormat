from __future__ import annotations

import logging

from ..core import photo_mark, vision as vision_core

logger = logging.getLogger("excelkro")


def vision_items(result, settings) -> list:
    """Преобразует строки кластеров результата в кандидатов vision."""
    return vision_core.items_from_rows(
        result.clusters or [],
        tuple(getattr(settings, "type_words", ())),
    )


def surplus_count(result, settings) -> int:
    """Количество строк с фактическим излишком, доступных vision."""
    return len(vision_core.surplus_items(vision_items(result, settings)))


def photo_notes(job, settings) -> tuple[bool, str]:
    rows = sorted(job.photo_rows)
    if not rows:
        return True, ""
    try:
        changed = photo_mark.apply(
            job.result.output_path,
            rows,
            sheet_name=settings.sheet_name,
        )
    except Exception as error:  # noqa: BLE001
        logger.exception("Пометки по фото не обновлены")
        return False, f"Файл не обновлён: {error}"
    return True, f"Строк с фото: {changed}."


def keep_answers(job, report: dict) -> int:
    """Добавляет ответы vision в состояние результата и возвращает число новых."""
    answers = list(report.get("results") or [])
    added = 0
    for answer in answers:
        for candidate in getattr(answer, "candidates", None) or []:
            number = int(getattr(candidate, "row", 0) or 0)
            if number > 0 and number not in job.photo_rows:
                job.photo_rows.add(number)
                added += 1
    return added


def show_answers(job, report: dict) -> int:
    """Кладёт ответы модели на страницу «Фото товара»; возвращает число новых.

    Снимки писем разбираются в фоне, при обработке сверки. Раньше отчёт
    разбора никуда, кроме сообщения на странице сверки, не попадал: список
    на подтверждение страница «Фото товара» берёт из `job.photos`, а туда
    ответы не складывались — человек читал «узнано товаров: 6», а
    подтверждать было нечего.

    Снимки без кандидатов не показываем: подтверждать там нечего. Один и тот
    же снимок дважды в список не попадает — иначе пересборка файла или
    повторный разбор письма плодили бы карточки.
    """

    def key(item) -> tuple[str, str]:
        return (
            str(getattr(item, "photo", "") or ""),
            str(getattr(item, "digest", "") or ""),
        )

    added = 0
    for answer in report.get("results") or []:
        if not (getattr(answer, "candidates", None) or []):
            continue
        if any(key(item) == key(answer) for item in job.photos):
            continue
        job.photos.append(answer)
        added += 1
    return added


def drop_photo(job, photo: str, digest: str) -> None:
    def same(item) -> bool:
        return str(getattr(item, "photo", "")) == str(photo) and (
            not digest or not getattr(item, "digest", "") or str(getattr(item, "digest", "")) == str(digest)
        )
    job.photos[:] = [item for item in job.photos if not same(item)]
