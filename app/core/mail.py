"""Приём фото и сообщений ревизоров из почтового ящика.

Ревизоры присылают снимки плюсующего товара письмом. Модуль забирает такие
письма по IMAP, складывает вложения на диск и достаёт из текста то, что
можно найти без всякой модели: коды товара, количества и слова вроде
«излишек» или «пересорт». Дальше снимки разбирает `vision.py`, а решение
принимает человек.

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
"""

from __future__ import annotations

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
TEXT_LIMIT = 1200

UNSAFE_NAME = re.compile(r"[\\/\x00]+")

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
    base = UNSAFE_NAME.sub("", base).strip().strip(".")
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


# --- Разбор письма ---------------------------------------------------------


@dataclass
class Photo:
    """Снимок из письма."""

    name: str
    data: bytes = b""
    path: str = ""
    size: int = 0


@dataclass
class Letter:
    """Разобранное письмо ревизора."""

    uid: str
    sender: str = ""
    subject: str = ""
    day: dt.date | None = None
    text: str = ""
    photos: list[Photo] = field(default_factory=list)
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
    """Текст письма. HTML берём, только если простого текста нет."""
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
    body = "\n".join(plain) if plain else "\n".join(rich)
    return re.sub(r"[ \t\r\f\v]+", " ", body).strip()


def _photos(message: Message, limit_mb: int) -> tuple[list[Photo], list[str]]:
    """Снимки письма и заметки о пропущенных вложениях.

    `walk()` заходит и внутрь пересланных писем (`message/rfc822`), поэтому
    фото из пересылки тоже находятся.
    """
    found: list[Photo] = []
    notes: list[str] = []
    limit = max(1, int(limit_mb or 1)) * 1024 * 1024
    for part in message.walk():
        if part.is_multipart():
            continue
        kind = (part.get_content_type() or "").lower()
        name = safe_name(_decoded(part.get_filename()))
        looks_photo = kind.startswith("image/") or is_photo(name)
        if not looks_photo:
            continue
        if not is_photo(name):
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
        found.append(Photo(name=name, data=data, size=len(data)))
    return found, notes


def read_hints(text: str, subject: str = "") -> dict:
    """Что видно в письме без всякой модели: коды, количество, слова.

    Это сознательно простой разбор. Он не пытается понять письмо целиком,
    а только подсказывает человеку, к чему снимок относится.
    """
    whole = f"{subject}\n{text}".strip()
    low = whole.lower().replace("ё", "е")
    codes: list[str] = []
    for item in CODE.findall(whole):
        if item not in codes:
            codes.append(item)
    quantity = QUANTITY.search(whole)
    store = STORE.search(whole)
    words = [word for word in KEYWORDS if word in low]
    return {
        "codes": codes[:20],
        "quantity": quantity.group(1).replace(",", ".") if quantity else "",
        "store": store.group(1) if store else "",
        "words": words,
    }


def parse_letter(uid: str, raw: bytes, limit_mb: int) -> Letter:
    """Разбирает одно письмо целиком."""
    message = email.message_from_bytes(raw)
    sender = parseaddr(_decoded(message.get("From")))[1].lower()
    subject = _decoded(message.get("Subject"))
    day: dt.date | None = None
    try:
        stamp = parsedate_to_datetime(message.get("Date"))
        day = stamp.date() if stamp else None
    except (TypeError, ValueError):
        day = None
    text = _body(message)
    photos, notes = _photos(message, limit_mb)
    return Letter(
        uid=str(uid),
        sender=sender,
        subject=subject,
        day=day,
        text=text[:TEXT_LIMIT],
        photos=photos,
        hints=read_hints(text, subject),
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
    name = str(folder or "INBOX").strip() or "INBOX"
    try:
        status, _ = client.select(name)
    except imaplib.IMAP4.error as error:
        raise MailError(f"Папка {name} не открывается: {error}") from error
    if status != "OK":
        raise MailError(f"Папка {name} не найдена в ящике.")


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


def check(settings, opener=None) -> tuple[bool, str]:
    """Проверка ящика по кнопке: вход, папка и число свежих писем."""
    config = load_config(settings)
    try:
        client = (opener or connect)(config)
    except MailError as error:
        return False, str(error)
    try:
        _select(client, str(config.get("mail_folder") or "INBOX"))
        uids = _uids(client, config)
    except MailError as error:
        return False, str(error)
    finally:
        _logout(client)
    return True, (
        f"Ящик отвечает, папка {config.get('mail_folder')}: "
        f"писем к разбору {len(uids)}."
    )


def collect(settings, opener=None) -> dict:
    """Забирает письма и складывает снимки на диск.

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

            kept: list[Photo] = []
            for number, photo in enumerate(letter.photos, start=1):
                if photos_total >= max_photos:
                    letter.notes.append("часть снимков не забрана: предел за один заход")
                    break
                target = folder / uid / f"{number:02d}-{photo.name}"
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(photo.data)
                except OSError as error:
                    letter.notes.append(f"снимок {photo.name} не сохранён: {error}")
                    continue
                photo.path = str(target)
                photo.data = b""
                kept.append(photo)
                photos_total += 1
            letter.photos = kept
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
