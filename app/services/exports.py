"""Готовые файлы для программы-компаньона.

Архив письма в `app/core/mail.py` собирается по одному письму: на
странице сверки на каждое письмо своя кнопка. Компаньону нужен один
архив на всё задание: в папку рядом со сверкой ложатся снимки всех
писем своего магазина. Связка письма со сверкой та же, что и на
страницах, — `app/core/mail_match.py`, иначе в архив попадут чужие фото.

Архив пишется рядом с готовой сверкой: там же его уберёт срок
хранения вместе с рабочей папкой задания. Сначала пишется
временный `.part`, потом переименовывается: компаньон не должен
забрать недописанный zip.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

from ..core import mail_match

logger = logging.getLogger("excelkro")

# Знаки, недопустимые в именах файлов Windows: архив распакуется на
# рабочем компьютере, а не на сервере.
UNSAFE = re.compile(r"[\\/:*?\"<>|\x00]+")


def safe_part(text: object, fallback: str = "") -> str:
    cleaned = UNSAFE.sub(" ", str(text or "")).strip().strip(".").strip()
    return " ".join(cleaned.split()) or fallback


def archive_name(job) -> str:
    """Имя архива снимков: «Фото_склад_дата.zip»."""
    warehouse = safe_part(job.warehouse, "склад")
    date = safe_part((job.result.summary or {}).get("date"))
    tail = f"_{date}" if date else ""
    return f"Фото_{warehouse}{tail}.zip"


def letter_files(letter):
    """Вложения письма, которые ещё лежат на диске."""
    items = list(getattr(letter, "photos", None) or []) + list(getattr(letter, "files", None) or [])
    for item in items:
        raw = str(getattr(item, "path", "") or "")
        path = Path(raw) if raw else None
        if path is not None and path.is_file():
            yield item, path


def entry_name(item, path: Path, number: int) -> str:
    """Имя файла внутри архива: сначала номер, потом товар."""
    title = safe_part(getattr(item, "title", "") or getattr(item, "name", ""), path.name)
    suffix = path.suffix or ".jpg"
    if not title.lower().endswith(suffix.lower()):
        title = f"{title}{suffix}"
    return f"{number:02d}-{title}"


def photo_zip(job, letters, split=mail_match.split) -> Path | None:
    """Собирает архив снимков задания. Фото нет — возвращает `None`.

    `split` передаётся аргументом по тому же правилу, что `opener` и
    `recognizer` в почте: тесты подменяют его и не зависят от связки
    по магазину.
    """
    link = split(job.warehouse, list(getattr(letters, "items", None) or []))
    mine = list(link.get("mine") or []) if isinstance(link, dict) else []
    target = job.result.output_path.parent / archive_name(job)
    temporary = target.with_name(target.name + ".part")
    added = 0
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as pack:
            for letter in mine:
                uid = safe_part(getattr(letter, "uid", ""), "без номера")
                for number, (item, path) in enumerate(letter_files(letter), start=1):
                    pack.write(path, f"письмо-{uid}/{entry_name(item, path, number)}")
                    added += 1
    except OSError as error:
        logger.warning("Архив снимков не собран: %s", error, exc_info=True)
        temporary.unlink(missing_ok=True)
        return None
    if not added:
        temporary.unlink(missing_ok=True)
        return None
    temporary.replace(target)
    return target
