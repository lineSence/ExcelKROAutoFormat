"""Память о разобранных письмах между перезапусками службы.

Раньше письма жили только в памяти процесса (`MAIL_LETTERS` в
`app/main.py`). Из-за этого кнопки «Архив письма №…» на странице сверки
пропадали после каждого перезапуска, а вернуть их было нечем: номера
разобранных писем лежат в `mail-state.json`, поэтому повторный заход в
ящик честно отвечал «новых писем нет».

Здесь письма сохраняются в `mail-letters.json` рядом с остальными
данными: тема, отправитель, дата, текст, подсказки из текста и пути к
уже скачанным вложениям. Сами вложения лежат на диске в `mail-photos`,
в файл они не попадают. Письмо, у которого на диске не осталось ни
одного файла (снимки убрал срок хранения), при чтении отбрасывается:
архив из него собрать уже нельзя.

Кнопка «Удалить все письма» в разделе почты зовёт `forget_all`: список
писем, скачанные вложения и память о разобранных номерах убираются
разом. Номера забываются намеренно — иначе те же письма второй раз из
ящика уже не забрать, и страница осталась бы пустой.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from pathlib import Path

from . import runtime
from .mail import Letter, Photo, load_state, photos_dir, save_state

logger = logging.getLogger("excelkro.mail_store")

# Сколько писем помним. Кнопок архивов на странице сверки должно быть
# столько, сколько человек способен разглядеть.
LIMIT = 50


def store_path(settings) -> Path:
    return runtime.data_dir(settings) / "mail-letters.json"


def _photo_json(item: Photo) -> dict:
    """Вложение для записи в файл. Содержимое не пишем: оно на диске."""
    return {
        "name": str(item.name or ""),
        "path": str(item.path or ""),
        "size": int(item.size or 0),
        "title": str(item.title or ""),
    }


def _photo_from(data: object) -> Photo:
    values = data if isinstance(data, dict) else {}
    try:
        size = int(values.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return Photo(
        name=str(values.get("name") or ""),
        path=str(values.get("path") or ""),
        size=size,
        title=str(values.get("title") or ""),
    )


def letter_json(letter: Letter) -> dict:
    return {
        "uid": str(letter.uid or ""),
        "sender": str(letter.sender or ""),
        "subject": str(letter.subject or ""),
        "day": letter.day.isoformat() if letter.day else "",
        "text": str(letter.text or ""),
        "photos": [_photo_json(item) for item in letter.photos or []],
        "files": [_photo_json(item) for item in letter.files or []],
        "hints": dict(letter.hints or {}),
        "notes": [str(item) for item in letter.notes or []],
    }


def letter_from(data: object) -> Letter:
    values = data if isinstance(data, dict) else {}
    day: dt.date | None = None
    raw_day = str(values.get("day") or "").strip()
    if raw_day:
        try:
            day = dt.date.fromisoformat(raw_day)
        except ValueError:
            day = None
    hints = values.get("hints")
    return Letter(
        uid=str(values.get("uid") or ""),
        sender=str(values.get("sender") or ""),
        subject=str(values.get("subject") or ""),
        day=day,
        text=str(values.get("text") or ""),
        photos=[_photo_from(item) for item in values.get("photos") or []],
        files=[_photo_from(item) for item in values.get("files") or []],
        hints=dict(hints) if isinstance(hints, dict) else {},
        notes=[str(item) for item in values.get("notes") or []],
    )


def alive(letter: Letter) -> bool:
    """Остался ли на диске хоть один файл письма.

    Письмо без файлов на странице бесполезно: архив собрать не из чего.
    """
    for item in list(letter.photos or []) + list(letter.files or []):
        path = str(getattr(item, "path", "") or "")
        if path and Path(path).is_file():
            return True
    return False


def save(settings, letters: list) -> None:
    """Пишет письма в файл. Беда с диском не должна ронять страницу."""
    file = store_path(settings)
    payload = {
        "saved": dt.datetime.now().isoformat(timespec="seconds"),
        "letters": [letter_json(item) for item in list(letters)[:LIMIT]],
    }
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.warning("Письма не сохранены в %s", file, exc_info=True)


def load(settings) -> list[Letter]:
    """Читает письма из файла. Пропавшие с диска письма отбрасываются."""
    file = store_path(settings)
    if not file.is_file():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать %s", file)
        return []
    rows = data.get("letters") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    letters: list[Letter] = []
    for row in rows:
        letter = letter_from(row)
        if letter.uid and alive(letter):
            letters.append(letter)
    return letters[:LIMIT]


def remember(settings, letters: list) -> list[Letter]:
    """Складывает свежие письма к уже известным и сохраняет список.

    Свежие письма идут первыми, дальше — прежние, кроме тех же номеров.
    Так кнопки архивов не пропадают, когда новых писем в ящике нет.
    """
    fresh = list(letters)
    known = {str(item.uid) for item in fresh}
    merged = fresh + [item for item in load(settings) if str(item.uid) not in known]
    merged = merged[:LIMIT]
    save(settings, merged)
    return merged


def forget_all(settings) -> dict:
    """Удаляет все письма: список, скачанные вложения и номера писем.

    Это работа кнопки «Удалить все письма». Убираются три вещи:

    * `mail-letters.json` — список писем для страниц;
    * папка `mail-photos` — скачанные снимки и прочие вложения;
    * список `seen` в `mail-state.json` — память о разобранных номерах.

    Номера забываются нарочно: без этого те же письма из ящика второй раз
    не придут («новых писем нет»), и вернуть удалённое было бы нечем.
    Ответы нейросети по снимкам на странице «Фото товара» здесь не
    трогаются: их человек подтверждает сам.

    Беда с диском не роняет страницу: о ней рассказывается в `troubles`.
    """
    troubles: list[str] = []

    file = store_path(settings)
    try:
        file.unlink(missing_ok=True)
    except OSError as error:
        troubles.append(f"файл списка писем не удалён: {error}")

    folder = photos_dir(settings)
    files = 0
    if folder.is_dir():
        for item in folder.rglob("*"):
            if item.is_file():
                files += 1
        shutil.rmtree(folder, ignore_errors=True)
        if folder.is_dir():
            troubles.append(f"папка снимков очищена не целиком: {folder}")

    state = load_state(settings)
    state["seen"] = []
    try:
        save_state(settings, state)
    except OSError as error:
        troubles.append(f"память о разобранных письмах не очищена: {error}")

    return {"files": files, "troubles": troubles}
