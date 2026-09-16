"""Приём фото и сообщений ревизоров из почтового ящика.

Ревизоры присылают снимки плюсующего товара письмом. Модуль забирает такие
письма по IMAP, складывает вложения на диск и достаёт из текста то, что
можно найти без всякой модели: коды товара, количества, слова вроде
«излишек» или «пересорт», упоминание списка несосчитанного товара и ФИО
ночного продавца. Дальше снимки разбирает `vision.py`, а решение принимает
человек.

Почему именно IMAP и стандартная библиотека:

* `imaplib` и `email` уже есть в Python, новых зависимостей не нужно —
  в `requirements.txt` нет даже `requests`;
* Mail.ru и Яндекс доступны из России, в отличие от Gmail API;
* вход по паролю приложения, без OAuth-редиректов, которых у службы нет.

Важно о настройках: логин и пароль задаются только в интерфейсе и лежат
в `runtime.json` рядом с остальными переключателями (см. `runtime.py`).
Пароль нужен именно **отдельный пароль приложения** почтового ящика, а не
основной пароль от учётной записи.

Письма забираются по кнопке, а не сами по себе: состояние сверок живёт в
памяти процесса и теряется при перезапуске службы, поэтому фоновому сбору
было бы некуда складывать результат. Уже разобранные письма запоминаются
в `mail-state.json`, чтобы повторный заход не тянул одно и то же: флаг
«прочитано» для этого ненадёжен — ящик смотрит и человек.

Имена папок в IMAP пишутся не в UTF-8, а в особой кодировке из RFC 3501
(modified UTF-7). Поэтому папка «Отчёты» уезжает на сервер как
`&BB4EQgRHBRE-...`, а не как есть: иначе `imaplib` спотыкается на
кириллице ещё до отправки команды.

Текст письма хранится дважды и нарочно: `raw_text` — точная копия того,
что пришло в письме (она уходит в `Письмо.txt` архива), а `text` —
сжатый и обрезанный вариант для страницы. Архив письма собирает
`app/core/mail_archive.py`; здешние `build_archive`, `archive_name` и
`letter_text` остались тонкими обёртками для прежних вызовов.
"""

from __future__ import annotations

import base64
import datetime as dt
import email
import imaplib
import json
import logging
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from . import runtime

logger = logging.getLogger("excelkro.mail")


class MailError(Exception):
    """Понятная человеку ошибка работы с ящиком."""


# Настройки приёма почты и их значения по умолчанию.
# Ящик по умолчанию — Mail.ru: так решил владелец.
DEFAULTS: dict[str, object] = {
    "mail_enabled": False,
    "mail_host": "imap.mail.ru",
    "mail_port": 993,
    "mail_login": "",
    "mail_password": "",
    "mail_folder": "INBOX",
    # Через запятую: письма с других адресов не разбираются. Пусто — все.
    "mail_senders": "",
    "mail_only_unseen": True,
    "mail_since_days": 7,
    "mail_max_letters": 20,
    "mail_max_photos": 40,
    "mail_max_photo_mb": 15,
    "mail_timeout": 30.0,
    # Помечать разобранные письма прочитанными в самом ящике.
    "mail_mark_seen": True,
    # Сколько дней хранить скачанные снимки на диске.
    "mail_keep_days": 7,
}

KINDS: dict[str, type] = {
    "mail_enabled": bool,
    "mail_host": str,
    "mail_port": int,
    "mail_login": str,
    "mail_password": str,
    "mail_folder": str,
    "mail_senders": str,
    "mail_only_unseen": bool,
    "mail_since_days": int,
    "mail_max_letters": int,
    "mail_max_photos": int,
    "mail_max_photo_mb": int,
    "mail_timeout": float,
    "mail_mark_seen": bool,
    "mail_keep_days": int,
}

# Расширения, которые считаем снимком товара.
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp")

# Сколько разобранных писем помним, чтобы не тянуть их заново.
SEEN_LIMIT = 1000

# Сколько знаков текста письма показываем в интерфейсе.
# В архив уходит текст целиком: там обрезка недопустима.
TEXT_LIMIT = 1200

# Сколько имён папок показываем в подсказке об ошибке.
FOLDER_HINT_LIMIT = 30

UNSAFE_NAME = re.compile(r"[\\/]+")

# Хвост строки ответа LIST: имя папки в кавычках либо последнее слово.
FOLDER_LINE = re.compile(rb'"([^"]*)"\s*$')

# --- Детерминированный разбор текста --------------------------------------

# Код товара из столбца A: подряд идущие цифры.
CODE = re.compile(r"\b\d{5,10}\b")
# «3 шт», «10 штук», «2 уп».
QUANTITY = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:шт|штук|штуки|уп|упак)\b", re.IGNORECASE)
# «магазин 47», «маг. Ульянка», «тт Думская».
STORE = re.compile(
    r"(?:магазин|маг\.?|т\.?т\.?|точк\w*)\s*[№#]?\s*([0-9A-Za-zА-Яа-яЁё][\w\-]{1,30})",
    re.IGNORECASE,
)
# Слова, по которым видно, о чём письмо.
KEYWORDS = (
    "излишек",
    "излишк",
    "недостач",
    "пересорт",
    "брак",
    "списан",
    "возврат",
    "бой",
    "фото",
    "список",
    "в списке",
    "несосчитан",
    "продавец",
    "ночной продавец",
)

# Товар лежит в списке несосчитанного: об этом надо предупредить человека.
# Буква «ё» в тексте заранее заменяется на «е», поэтому пишем без неё.
LIST_WORDS = (
    "список",
    "в списке",
    "списке",
    "несосчитан",
    "не сосчитан",
    "не посчитан",
    "непосчитан",
)

# Слова, после которых в письме обычно стоит ФИО продавца.
SELLER_WORDS = ("ночной продавец", "продавец", "продавца", "продавцом")

# Слова ревизора: его ФИО в поле «ночной продавец» попадать не должно.
AUDITOR_WORDS = ("ревизор", "проверяющ", "с уважением")

# «Иванов Иван Иванович» и «Иванов И. И.» — два обычных вида ФИО в письме.
FULL_NAME = re.compile(
    r"\b([А-ЯЁ][а-яё]{1,20})\s+([А-ЯЁ][а-яё]{1,20})(?:\s+([А-ЯЁ][а-яё]{1,20}))?\b"
)
SHORT_NAME = re.compile(r"\b([А-ЯЁ][а-яё]{1,20})\s+([А-ЯЁ])\.\s*([А-ЯЁ])?\.?")

# Слова, похожие на фамилию по написанию, но фамилией не являющиеся.
NOT_NAME = (
    "ночной",
    "ночная",
    "продавец",
    "продавца",
    "ревизор",
    "ревизора",
    "магазин",
    "список",
    "товар",
    "добрый",
    "здравствуйте",
    "привет",
    "спасибо",
    "уважением",
    "смена",
    "фото",
)


def mask_secret(value: str) -> str:
    """Хвост пароля для показа в интерфейсе. Целиком его не показываем."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "…" + text[-1:]
    return "…" + text[-4:]


def safe_name(name: object) -> str:
    """Имя вложения без пути: письмо может принести «../../passwd»."""
    base = Path(str(name or "")).name
    base = UNSAFE_NAME.sub("", base).replace(chr(0), "").strip().strip(".")
    return base or "photo.jpg"


def is_photo(name: str) -> bool:
    return str(name or "").lower().endswith(PHOTO_EXT)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _typed(name: str, value):
    kind = KINDS[name]
    if kind is bool:
        return _as_bool(value)
    if kind is str:
        return str(value).strip()
    text = str(value).strip().replace(",", ".")
    if kind is int:
        return int(float(text))
    return float(text)


def load_config(settings) -> dict:
    """Настройки приёма почты: значения по умолчанию плюс `runtime.json`."""
    stored = runtime.load(runtime.runtime_path(settings))
    config = dict(DEFAULTS)
    for name in KINDS:
        if name not in stored:
            continue
        try:
            config[name] = _typed(name, stored[name])
        except (TypeError, ValueError):
            logger.warning("Значение %s в runtime.json не понятно, берётся прежнее", name)
    return config


def save_config(settings, values: dict) -> dict:
    """Сохраняет настройки из формы.

    Пустое текстовое поле значит «оставить как было»: пароль не нужно
    вводить заново при каждой правке.
    """
    path = runtime.runtime_path(settings)
    stored = runtime.load(path)
    for name, value in values.items():
        if name not in KINDS:
            continue
        if KINDS[name] is bool:
            stored[name] = _as_bool(value)
            continue
        if value is None or not str(value).strip():
            continue
        try:
            stored[name] = _typed(name, value)
        except (TypeError, ValueError):
            logger.warning("Значение %s не сохранено: не число", name)
    runtime.save(stored, path)
    return stored


def forget_password(settings) -> None:
    """Удаляет пароль ящика из настроек."""
    path = runtime.runtime_path(settings)
    stored = runtime.load(path)
    stored["mail_password"] = ""
    runtime.save(stored, path)


def photos_dir(settings) -> Path:
    return runtime.data_dir(settings) / "mail-photos"


def state_path(settings) -> Path:
    return runtime.data_dir(settings) / "mail-state.json"


def load_state(settings) -> dict:
    file = state_path(settings)
    if not file.is_file():
        return {"seen": []}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать %s", file)
        return {"seen": []}
    if not isinstance(data, dict):
        return {"seen": []}
    data.setdefault("seen", [])
    return data


def save_state(settings, state: dict) -> None:
    file = state_path(settings)
    file.parent.mkdir(parents=True, exist_ok=True)
    seen = [str(item) for item in state.get("seen", [])][-SEEN_LIMIT:]
    payload = dict(state)
    payload["seen"] = seen
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_photos(settings, days: int = 0) -> int:
    """Убирает старые скачанные снимки. Возвращает число удалённых файлов."""
    folder = photos_dir(settings)
    if not folder.is_dir():
        return 0
    keep = int(days or 0)
    if keep <= 0:
        return 0
    edge = dt.datetime.now().timestamp() - keep * 86400
    gone = 0
    for file in folder.rglob("*"):
        if not file.is_file():
            continue
        try:
            if file.stat().st_mtime < edge:
                file.unlink()
                gone += 1
        except OSError:
            logger.warning("Старый снимок не удалён: %s", file)
    return gone


# --- Имена папок в кодировке IMAP -----------------------------------------


def encode_folder(name: str) -> bytes:
    """Имя папки в modified UTF-7 (RFC 3501).

    Обычные знаки идут как есть, всё остальное (кириллица, эмодзи) —
    в base64 от UTF-16BE между «&» и «-». Символ «&» удваивается в «&-».
    Без этого `imaplib` пытается перевести «Отчёты» в ASCII и падает
    ещё до разговора с сервером.
    """
    out = bytearray()
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        raw = "".join(buffer).encode("utf-16-be")
        chunk = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.extend(b"&" + chunk.encode("ascii") + b"-")
        buffer.clear()

    for letter_ in str(name or ""):
        if letter_ == "&":
            flush()
            out.extend(b"&-")
        elif " " <= letter_ <= "~":
            flush()
            out.extend(letter_.encode("ascii"))
        else:
            buffer.append(letter_)
    flush()
    return bytes(out)


def decode_folder(raw: object) -> str:
    """Имя папки из modified UTF-7 обратно в читаемый вид."""
    text = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else str(raw or "")
    out: list[str] = []
    position = 0
    while position < len(text):
        sign = text[position]
        if sign != "&":
            out.append(sign)
            position += 1
            continue
        end = text.find("-", position + 1)
        if end < 0:
            out.append(text[position:])
            break
        chunk = text[position + 1 : end]
        if not chunk:
            out.append("&")
        else:
            data = chunk.replace(",", "/")
            data += "=" * (-len(data) % 4)
            try:
                out.append(base64.b64decode(data).decode("utf-16-be"))
            except (ValueError, UnicodeDecodeError):
                out.append(text[position : end + 1])
        position = end + 1
    return "".join(out)


def quote_folder(name: str) -> bytes:
    """Имя папки для команды SELECT: закодировано и в кавычках.

    Кавычки нужны из-за пробелов в названиях вроде «Отчёты ревизоров»:
    без них сервер прочитает только первое слово.
    """
    encoded = encode_folder(name).replace(b"\\", b"\\\\").replace(b'"', b'\\"')
    return b'"' + encoded + b'"'


def list_folders(client) -> list[str]:
    """Имена папок ящика в читаемом виде. При беде — пустой список."""
    try:
        status, data = client.list()
    except (imaplib.IMAP4.error, OSError, AttributeError):
        logger.debug("Список папок не получен", exc_info=True)
        return []
    if status != "OK" or not data:
        return []
    names: list[str] = []
    for line in data:
        if line is None:
            continue
        raw = line if isinstance(line, bytes) else str(line).encode("utf-8", "replace")
        found = FOLDER_LINE.search(raw)
        piece = found.group(1) if found else raw.split()[-1]
        name = decode_folder(piece).strip()
        if name and name not in names:
            names.append(name)
    return names


def _folders_hint(client) -> str:
    names = list_folders(client)
    if not names:
        return ""
    shown = ", ".join(names[:FOLDER_HINT_LIMIT])
    return f" Папки ящика: {shown}."


# --- Разбор письма ---------------------------------------------------------


@dataclass
class Photo:
    """Вложение письма: снимок или обычный файл."""

    name: str
    data: bytes = b""
    path: str = ""
    size: int = 0
    # Имя, которое дала нейросеть: под ним снимок кладётся в архив письма.
    title: str = ""


@dataclass
class Letter:
    """Разобранное письмо ревизора."""

    uid: str
    sender: str = ""
    subject: str = ""
    day: dt.date | None = None
    # Точное время письма: по нему называется архив.
    at: dt.datetime | None = None
    # Текст для страницы: сжатые пробелы и обрезка по TEXT_LIMIT.
    text: str = ""
    # Текст письма как есть: он уходит в «Письмо.txt» архива.
    raw_text: str = ""
    photos: list[Photo] = field(default_factory=list)
    # Вложения, которые снимками не являются: акты, таблицы, документы.
    files: list[Photo] = field(default_factory=list)
    hints: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _decoded(value: object) -> str:
    """Заголовок письма в читаемом виде: он бывает в RFC 2047."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except (ValueError, LookupError, UnicodeDecodeError):
        return str(value).strip()


def _body(message: Message) -> str:
    """Текст письма как есть. HTML берём, только если простого текста нет.

    Ничего не чистится нарочно: этот текст уходит в `Письмо.txt` архива и
    должен быть точной копией письма. Сжатие пробелов и обрезка нужны
    только странице — это делает `_tidy`.
    """
    plain: list[str] = []
    rich: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        kind = (part.get_content_type() or "").lower()
        if kind not in ("text/plain", "text/html"):
            continue
        if (part.get_content_disposition() or "") == "attachment":
            continue
        raw = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        if kind == "text/plain":
            plain.append(text)
        else:
            rich.append(re.sub(r"<[^>]+>", " ", text))
    return "\n".join(plain) if plain else "\n".join(rich)


def _tidy(text: str) -> str:
    """Текст для страницы: лишние пробелы убраны, переносы строк целы."""
    return re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()


def attachments(message: Message, limit_mb: int) -> tuple[list[Photo], list[Photo], list[str]]:
    """Вложения письма: снимки, прочие файлы и заметки о пропущенных.

    Прочие файлы тоже нужны: они уходят в архив письма целиком.
    `walk()` заходит и внутрь пересланных писем (`message/rfc822`), поэтому
    фото из пересылки тоже находятся.
    """
    photos: list[Photo] = []
    files: list[Photo] = []
    notes: list[str] = []
    limit = max(1, int(limit_mb or 1)) * 1024 * 1024
    for part in message.walk():
        if part.is_multipart():
            continue
        kind = (part.get_content_type() or "").lower()
        raw_name = _decoded(part.get_filename())
        disposition = (part.get_content_disposition() or "").lower()
        looks_photo = kind.startswith("image/") or is_photo(safe_name(raw_name))
        # Тело письма вложением не считаем: оно уходит в текст.
        if not str(raw_name).strip() and not looks_photo:
            continue
        if kind in ("text/plain", "text/html") and disposition != "attachment":
            continue
        name = safe_name(raw_name)
        if looks_photo and not is_photo(name):
            # Встроенная картинка без имени: достраиваем по типу.
            tail = kind.split("/")[-1].split(";")[0] or "jpg"
            name = f"{name}.{tail}"
        data = part.get_payload(decode=True) or b""
        if not data:
            notes.append(f"вложение {name} пустое")
            continue
        if len(data) > limit:
            notes.append(f"вложение {name} больше {limit_mb} МБ")
            continue
        item = Photo(name=name, data=data, size=len(data))
        if looks_photo:
            photos.append(item)
        else:
            files.append(item)
    return photos, files, notes


def _clean_name(parts) -> bool:
    """Похоже ли найденное на ФИО, а не на обычные слова с большой буквы."""
    for part in parts:
        if not part:
            continue
        if part.lower().replace("ё", "е") in NOT_NAME:
            return False
    return True


def _name_in(line: str) -> str:
    """ФИО из одной строки письма. Пусто, если ничего похожего нет."""
    short = SHORT_NAME.search(line)
    if short is not None and _clean_name((short.group(1),)):
        tail = " ".join(part + "." for part in short.groups()[1:] if part)
        return f"{short.group(1)} {tail}".strip()
    full = FULL_NAME.search(line)
    if full is not None and _clean_name(full.groups()):
        return " ".join(part for part in full.groups() if part)
    return ""


def find_seller(text: str, subject: str = "") -> str:
    """ФИО продавца из письма. Ревизора сюда не берём.

    Ищем в строках, где есть слово «продавец»: сначала в самой строке,
    затем в следующей — ревизоры часто пишут «Ночной продавец:» и ФИО
    на новой строке. Строки про ревизоров и подпись письма пропускаем.
    """
    lines = [line.strip() for line in f"{subject}\n{text}".splitlines()]
    for index, line in enumerate(lines):
        low = line.lower().replace("ё", "е")
        if not any(word in low for word in SELLER_WORDS):
            continue
        if any(word in low for word in AUDITOR_WORDS):
            continue
        tail = line
        for mark in (":", "—", "-"):
            if mark in tail:
                tail = tail.split(mark, 1)[1]
                break
        name = _name_in(tail) or _name_in(line)
        if name:
            return name
        if index + 1 < len(lines):
            following = lines[index + 1]
            low_next = following.lower().replace("ё", "е")
            if any(word in low_next for word in AUDITOR_WORDS):
                continue
            name = _name_in(following)
            if name:
                return name
    return ""


def read_hints(text: str, subject: str = "") -> dict:
    """Что видно в письме без всякой модели: коды, количество, слова.

    Это сознательно простой разбор. Он не пытается понять письмо целиком,
    а только подсказывает человеку, к чему снимок относится и что стоит
    проверить: товар в списке несосчитанного и ФИО ночного продавца.
    """
    whole = f"{subject}\n{text}".strip()
    low = whole.lower().replace("ё", "е")
    codes: list[str] = []
    for item in CODE.findall(whole):
        if item not in codes:
            codes.append(item)
    quantity = QUANTITY.search(whole)
    store = STORE.search(whole)
    words = [word for word in KEYWORDS if word.replace("ё", "е") in low]
    in_list = [word for word in LIST_WORDS if word in low]
    seller_said = any(word in low for word in SELLER_WORDS)
    return {
        "codes": codes[:20],
        "quantity": quantity.group(1).replace(",", ".") if quantity else "",
        "store": store.group(1) if store else "",
        "words": words,
        # Товар лежит в списке несосчитанного — об этом предупреждаем.
        "in_list": bool(in_list),
        "list_words": in_list,
        "seller_said": seller_said,
        "seller": find_seller(text, subject) if seller_said else "",
    }


def parse_letter(uid: str, raw: bytes, limit_mb: int) -> Letter:
    """Разбирает одно письмо целиком.

    Текст сохраняется двумя видами: `raw_text` — как пришло (уйдёт в
    `Письмо.txt`), `text` — сжатый и обрезанный для страницы.
    """
    message = email.message_from_bytes(raw)
    sender = parseaddr(_decoded(message.get("From")))[1].lower()
    subject = _decoded(message.get("Subject"))
    stamp: dt.datetime | None = None
    try:
        stamp = parsedate_to_datetime(message.get("Date"))
    except (TypeError, ValueError):
        stamp = None
    body = _body(message)
    shown = _tidy(body)
    photos, files, notes = attachments(message, limit_mb)
    return Letter(
        uid=str(uid),
        sender=sender,
        subject=subject,
        day=stamp.date() if stamp else None,
        at=stamp,
        text=shown[:TEXT_LIMIT],
        raw_text=body,
        photos=photos,
        files=files,
        hints=read_hints(shown, subject),
        notes=notes,
    )


def allowed_sender(sender: str, senders: str) -> bool:
    """Проверка белого списка адресов. Пустой список — принимаем всех."""
    allowed = [item.strip().lower() for item in str(senders or "").split(",") if item.strip()]
    if not allowed:
        return True
    address = str(sender or "").strip().lower()
    for item in allowed:
        if item.startswith("@"):
            if address.endswith(item):
                return True
        elif address == item:
            return True
    return False


# --- Архив письма ----------------------------------------------------------
# Сама сборка живёт в `mail_archive.py`: модуль почты и без неё крупный.
# Импорт отложенный, иначе получится кольцо: `mail_archive` берёт отсюда
# `Letter`, `photos_dir` и `safe_name`.


def archive_name(letter: Letter) -> str:
    """Имя файла архива письма (см. `mail_archive.archive_name`)."""
    from .mail_archive import archive_name as name_of

    return name_of(letter)


def letter_text(letter: Letter) -> str:
    """Содержимое «Письмо.txt» (см. `mail_archive.letter_text`)."""
    from .mail_archive import letter_text as text_of

    return text_of(letter)


def build_archive(settings, letter: Letter, target: Path | None = None) -> Path:
    """Собирает архив письма (см. `mail_archive.build_archive`)."""
    from .mail_archive import build_archive as build

    return build(settings, letter, target)


# --- Работа с ящиком -------------------------------------------------------


def connect(config: dict):
    """Открывает ящик. Любая беда превращается в понятную ошибку."""
    host = str(config.get("mail_host") or "").strip()
    login = str(config.get("mail_login") or "").strip()
    password = str(config.get("mail_password") or "")
    if not host:
        raise MailError("Не указан адрес почтового сервера.")
    if not login or not password:
        raise MailError("Не заданы почтовый ящик и пароль приложения.")
    try:
        client = imaplib.IMAP4_SSL(
            host,
            int(config.get("mail_port") or 993),
            timeout=float(config.get("mail_timeout") or 30.0),
        )
    except OSError as error:
        raise MailError(f"Почтовый сервер {host} не отвечает: {error}") from error
    try:
        client.login(login, password)
    except imaplib.IMAP4.error as error:
        _logout(client)
        raise MailError(
            "Ящик не пускает: проверьте логин и пароль приложения. "
            "Обычный пароль от учётной записи здесь не подходит. "
            f"Ответ сервера: {error}"
        ) from error
    return client


def _logout(client) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001
        logger.debug("Ящик закрылся с ошибкой", exc_info=True)


def _select(client, folder: str) -> None:
    """Открывает папку ящика.

    Имя кодируется в modified UTF-7: папка «Отчёты» иначе роняет `imaplib`
    на попытке перевести кириллицу в ASCII, и страница отдавала 500.
    Если папки нет, в ошибку кладём список настоящих имён — так видно,
    что у Mail.ru папка зовётся, например, «Отчёты», а не «отчеты».
    """
    name = str(folder or "INBOX").strip() or "INBOX"
    try:
        status, _ = client.select(quote_folder(name))
    except (imaplib.IMAP4.error, UnicodeError, ValueError, OSError) as error:
        raise MailError(
            f"Папка «{name}» не открывается: {error}.{_folders_hint(client)}"
        ) from error
    if status != "OK":
        raise MailError(f"Папка «{name}» не найдена в ящике.{_folders_hint(client)}")


def _uids(client, config: dict) -> list[str]:
    """Список писем к разбору: свежие и, при желании, только непрочитанные."""
    days = max(0, int(config.get("mail_since_days") or 0))
    criteria: list[str] = []
    if _as_bool(config.get("mail_only_unseen")):
        criteria.append("UNSEEN")
    if days:
        since = dt.date.today() - dt.timedelta(days=days)
        criteria.extend(["SINCE", since.strftime("%d-%b-%Y")])
    if not criteria:
        criteria.append("ALL")
    try:
        status, data = client.uid("search", None, *criteria)
    except imaplib.IMAP4.error as error:
        raise MailError(f"Поиск писем не удался: {error}") from error
    if status != "OK" or not data or data[0] is None:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    return str(raw).split()


def _raw_letter(client, uid: str) -> bytes:
    try:
        status, data = client.uid("fetch", uid, "(RFC822)")
    except imaplib.IMAP4.error as error:
        raise MailError(f"Письмо {uid} не читается: {error}") from error
    if status != "OK" or not data:
        return b""
    for item in data:
        if isinstance(item, tuple) and len(item) > 1 and item[1]:
            return bytes(item[1])
    return b""


def _mark_seen(client, uid: str) -> None:
    try:
        client.uid("store", uid, "+FLAGS", "(\\Seen)")
    except imaplib.IMAP4.error:
        logger.warning("Письмо %s не помечено прочитанным", uid)


def folders(settings, opener=None) -> list[str]:
    """Имена папок ящика. Нужны, чтобы подсказать человеку правильное."""
    config = load_config(settings)
    client = (opener or connect)(config)
    try:
        return list_folders(client)
    finally:
        _logout(client)


def check(settings, opener=None) -> tuple[bool, str]:
    """Проверка ящика по кнопке: вход, папка и число свежих писем.

    Наружу отдаётся только пара «получилось, что сказать человеку»:
    любая неожиданная беда тоже превращается в текст, иначе страница
    падала бы с Internal server error.
    """
    config = load_config(settings)
    try:
        client = (opener or connect)(config)
    except MailError as error:
        return False, str(error)
    except Exception as error:  # noqa: BLE001
        logger.warning("Ящик не открылся", exc_info=True)
        return False, f"Ящик не открылся: {error}"
    try:
        _select(client, str(config.get("mail_folder") or "INBOX"))
        uids = _uids(client, config)
    except MailError as error:
        return False, str(error)
    except Exception as error:  # noqa: BLE001
        logger.warning("Проверка ящика не удалась", exc_info=True)
        return False, f"Проверка не удалась: {error}"
    finally:
        _logout(client)
    return True, (
        f"Ящик отвечает, папка {config.get('mail_folder')}: "
        f"писем к разбору {len(uids)}."
    )


def _store_file(folder: Path, number: int, item: Photo, prefix: str = "") -> str:
    """Кладёт вложение на диск. Возвращает текст ошибки или пустую строку."""
    target = folder / f"{prefix}{number:02d}-{item.name}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.data)
    except OSError as error:
        return f"вложение {item.name} не сохранено: {error}"
    item.path = str(target)
    item.data = b""
    return ""


def collect(settings, opener=None) -> dict:
    """Забирает письма и складывает вложения на диск.

    Возвращает отчёт для страницы: разобранные письма, число снимков и
    список пропущенных писем с причинами.
    """
    config = load_config(settings)
    if not _as_bool(config.get("mail_enabled")):
        raise MailError("Приём почты выключен. Включите его в настройках раздела.")

    state = load_state(settings)
    seen: list[str] = [str(item) for item in state.get("seen", [])]
    known = set(seen)

    client = (opener or connect)(config)
    letters: list[Letter] = []
    skipped: list[str] = []
    photos_total = 0
    files_total = 0
    max_letters = max(1, int(config.get("mail_max_letters") or 1))
    max_photos = max(1, int(config.get("mail_max_photos") or 1))
    limit_mb = int(config.get("mail_max_photo_mb") or 15)
    folder = photos_dir(settings)

    try:
        _select(client, str(config.get("mail_folder") or "INBOX"))
        for uid in _uids(client, config):
            if len(letters) >= max_letters:
                skipped.append(f"письмо {uid}: предел писем за один заход")
                continue
            if uid in known:
                continue
            raw = _raw_letter(client, uid)
            if not raw:
                skipped.append(f"письмо {uid}: пустой ответ сервера")
                continue
            letter = parse_letter(uid, raw, limit_mb)
            if not allowed_sender(letter.sender, str(config.get("mail_senders") or "")):
                skipped.append(f"письмо от {letter.sender or 'неизвестного адреса'}: не в списке")
                seen.append(uid)
                known.add(uid)
                continue

            box = folder / safe_name(uid)
            kept: list[Photo] = []
            for number, photo in enumerate(letter.photos, start=1):
                if photos_total >= max_photos:
                    letter.notes.append("часть снимков не забрана: предел за один заход")
                    break
                trouble = _store_file(box, number, photo)
                if trouble:
                    letter.notes.append(trouble)
                    continue
                kept.append(photo)
                photos_total += 1
            letter.photos = kept

            # Прочие вложения тоже сохраняем: они уходят в архив письма.
            saved: list[Photo] = []
            for number, item in enumerate(letter.files, start=1):
                trouble = _store_file(box, number, item, prefix="doc-")
                if trouble:
                    letter.notes.append(trouble)
                    continue
                saved.append(item)
                files_total += 1
            letter.files = saved

            letters.append(letter)
            seen.append(uid)
            known.add(uid)
            if _as_bool(config.get("mail_mark_seen")):
                _mark_seen(client, uid)
    finally:
        _logout(client)

    state["seen"] = seen
    state["last"] = dt.datetime.now().isoformat(timespec="seconds")
    try:
        save_state(settings, state)
    except OSError:
        logger.warning("Состояние почты не сохранено", exc_info=True)

    cleaned = clean_photos(settings, int(config.get("mail_keep_days") or 0))
    return {
        "letters": letters,
        "photos": photos_total,
        "files": files_total,
        "skipped": skipped,
        "cleaned": cleaned,
    }


def status(settings, config: dict | None = None) -> dict:
    """Состояние приёма почты для интерфейса."""
    config = config or load_config(settings)
    password = str(config.get("mail_password") or "")
    login = str(config.get("mail_login") or "").strip()
    enabled = _as_bool(config.get("mail_enabled"))
    ready = bool(login and password and str(config.get("mail_host") or "").strip())
    state = load_state(settings)

    if not enabled:
        reason = "Выключен: письма не забираются."
    elif ready:
        reason = (
            f"Включён: {login}, папка {config.get('mail_folder')}. "
            "Письма забираются по кнопке «Забрать почту»."
        )
    elif not login:
        reason = "Включён, но не указан почтовый ящик."
    else:
        reason = "Включён, но не введён пароль приложения."

    return {
        "enabled": enabled,
        "ready": ready,
        "host": str(config.get("mail_host") or ""),
        "port": int(config.get("mail_port") or 993),
        "login": login,
        "folder": str(config.get("mail_folder") or "INBOX"),
        "senders": str(config.get("mail_senders") or ""),
        "only_unseen": _as_bool(config.get("mail_only_unseen")),
        "since_days": int(config.get("mail_since_days") or 0),
        "max_letters": int(config.get("mail_max_letters") or 0),
        "max_photos": int(config.get("mail_max_photos") or 0),
        "max_photo_mb": int(config.get("mail_max_photo_mb") or 0),
        "timeout": float(config.get("mail_timeout") or 30.0),
        "mark_seen": _as_bool(config.get("mail_mark_seen")),
        "keep_days": int(config.get("mail_keep_days") or 0),
        "has_password": bool(password),
        "password_tail": mask_secret(password),
        "last": str(state.get("last") or ""),
        "seen": len(state.get("seen", [])),
        "folder_path": str(photos_dir(settings)),
        "reason": reason,
    }
