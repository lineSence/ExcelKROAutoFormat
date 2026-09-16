"""Архив письма: имя файла и сборка zip.

Вынесено из `app/core/mail.py`: модуль почты и без этого крупный
([DEV-FILE-SIZE]).

Две вещи здесь решены нарочно и менять их без слова владельца нельзя:

* **Имя архива** — дата и время самого письма: `22-07-2026_16-08-04.zip`
  (правка владельца 16.09.2026). По имени видно, когда ревизор отправил
  письмо, а файлы в папке сами встают по времени. Если времени у письма
  нет (старая запись в `mail-letters.json`), берётся его дата, а если и
  даты нет — время сборки архива.
* **`Письмо.txt`** — точная копия текста письма из почты: ни шапки, ни
  подсказок, ни обрезки, ни сжатия пробелов. Тема, отправитель и
  подсказки человеку уже видны на странице почты, а в файле нужен сам
  текст, который можно переслать или сравнить с письмом в ящике.

Снимки внутри архива названы товаром, который узнала нейросеть: по имени
файла видно товар, не открывая фото.
"""

from __future__ import annotations

import datetime as dt
import logging
import zipfile
from pathlib import Path

from .mail import Letter, Photo, photos_dir, safe_name

logger = logging.getLogger("excelkro.mail_archive")

# Имя текстового файла с письмом внутри архива.
LETTER_FILE = "Письмо.txt"

# Вид метки времени в имени архива: «22-07-2026_16-08-04».
NAME_STAMP = "%d-%m-%Y_%H-%M-%S"


def stamp_name(moment: object = None) -> str:
    """Метка времени для имени файла. Без времени — «сейчас»."""
    if isinstance(moment, dt.datetime):
        when = moment
    elif isinstance(moment, dt.date):
        when = dt.datetime.combine(moment, dt.time())
    else:
        when = dt.datetime.now()
    return when.strftime(NAME_STAMP)


def letter_moment(letter) -> dt.datetime | None:
    """Время письма: сначала точное, потом дата, иначе ничего.

    Точного времени нет у писем, записанных прежними версиями службы:
    в `mail-letters.json` тогда хранилась только дата.
    """
    at = getattr(letter, "at", None)
    if isinstance(at, dt.datetime):
        return at
    day = getattr(letter, "day", None)
    if isinstance(day, dt.date):
        return dt.datetime.combine(day, dt.time())
    return None


def archive_name(letter) -> str:
    """Имя файла архива: дата и время письма, «22-07-2026_16-08-04.zip»."""
    return safe_name(stamp_name(letter_moment(letter)) + ".zip")


def letter_text(letter) -> str:
    """Содержимое `Письмо.txt`: ровно тот текст, что пришёл в письме.

    Ничего не дописывается и не чистится. Обрезанный текст для страницы
    (`letter.text`) берётся только как запасной вариант — у писем,
    сохранённых прежними версиями службы, полного текста на диске нет.
    """
    raw = str(getattr(letter, "raw_text", "") or "")
    if raw:
        return raw
    return str(getattr(letter, "text", "") or "")


def entry_name(item: Photo, number: int, used: set) -> str:
    """Имя файла внутри архива.

    У снимка берётся имя от нейросети (`title`), у остального — своё.
    Номер впереди сохраняет порядок вложений и разводит одинаковые имена.
    """
    suffix = Path(item.name).suffix or ".jpg"
    base = Path(safe_name(item.title)).stem if str(item.title).strip() else Path(item.name).stem
    name = f"{number:02d}-{base}{suffix}"
    while name in used:
        number += 1
        name = f"{number:02d}-{base}{suffix}"
    used.add(name)
    return name


def build_archive(settings, letter: Letter, target: Path | None = None) -> Path:
    """Складывает архив письма: все вложения плюс текст письма.

    Файлы берутся с диска: в памяти после захода в ящик их уже нет.
    Снимки попадают в архив под именем, которое дала нейросеть.
    """
    folder = photos_dir(settings) / safe_name(letter.uid)
    file = Path(target) if target is not None else folder / archive_name(letter)
    file.parent.mkdir(parents=True, exist_ok=True)

    used: set = set()
    with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(LETTER_FILE, letter_text(letter))
        for number, item in enumerate(list(letter.photos) + list(letter.files), start=1):
            data = item.data
            if not data and item.path:
                try:
                    data = Path(item.path).read_bytes()
                except OSError:
                    logger.warning("Вложение %s не прочитано с диска", item.path)
                    continue
            if not data:
                continue
            archive.writestr(entry_name(item, number, used), data)
    return file
