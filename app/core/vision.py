"""Распознавание фото плюсующего товара и подбор строки сверки.

Ревизоры присылают фото товара, который оказался в излишке. Задача модуля —
не узнать товар вообще, а выбрать его из короткого списка кандидатов:
строки текущей сверки, где `F > 0`.

Порядок работы:

1. Кандидаты берутся из уже разобранной сверки (строки с излишком).
2. Снимок уходит в хранилище файлов GigaChat, затем его идентификатор и
   список имён — одним запросом на генерацию.
3. Модель возвращает прочитанный с пачки текст и до трёх номеров строк.
4. Ответ сверяется с кандидатами по имени (`resort.normalize`,
   `resort.similarity`): если модель назвала строку, которой нет в списке,
   или ошиблась номером, подбор делается по прочитанному тексту.
5. Результат — подсказка человеку. В файл сверки ничего не пишется:
   решение человека главнее модели.

Провайдер — GigaChat (Сбер): доступен из России, у версии для физических лиц
есть бесплатные токены. Авторизация двухшаговая: ключ авторизации (Basic)
меняется на токен доступа на 30 минут, токен держится в памяти процесса.
Версия API (`scope`) подбирается сама: сначала версия для физлиц, затем B2B
и корпоративная, найденное значение запоминается в файле настроек.

Настройки не берутся из `.env`: они задаются на странице «Фото товара» и
хранятся в `data/runtime.json` рядом с переключателем эмбеддингов. Там же
лежит ключ авторизации — вводится один раз в интерфейсе, перезапуск не нужен.

Сетевые вызовы сделаны на `urllib` из стандартной библиотеки: новых
зависимостей нет, на сервере с 1 ГБ памяти ничего не разворачивается.
Проверка сертификата по умолчанию отключена: у Сбера цепочка Минцифры,
которой в системном хранилище обычно нет.

Модуль всегда отвечает объектом `PhotoResult`. Нет ключа, нет сети, кончились
токены, битый ответ — всё это возвращается полем `error`, а не исключением:
страница должна открываться в любом случае.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import resort, runtime

logger = logging.getLogger("excelkro.vision")

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"

# Версии API GigaChat. Ключ авторизации выдаётся под одну из них, поэтому
# перебираем по очереди и запоминаем ту, которая ответила.
SCOPES = ("GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP")

# Значения по умолчанию. Всё это меняется на странице «Фото товара».
DEFAULTS: dict = {
    "vision_enabled": False,
    "vision_model": "GigaChat-2",
    # Ключ авторизации из личного кабинета GigaChat (строка Basic).
    "vision_api_key": "",
    "vision_scope": SCOPES[0],
    "vision_verify_ssl": False,
    # Сколько строк-излишков показывать модели за один запрос.
    "vision_max_rows": 40,
    "vision_max_photo_mb": 8,
    "vision_timeout": 60,
    "vision_retries": 2,
    # Пауза между снимками: у бесплатной версии есть предел обращений.
    "vision_pause_seconds": 4.0,
    # Ниже этой схожести запасной подбор по тексту строку не предлагает.
    "vision_text_min_score": 0.55,
    "vision_cache_limit": 2000,
}

BOOL_KEYS = ("vision_enabled", "vision_verify_ssl")
# Ключи, которые хранятся числом с плавающей точкой.
FLOAT_KEYS = ("vision_pause_seconds", "vision_text_min_score")
INT_KEYS = (
    "vision_max_rows",
    "vision_max_photo_mb",
    "vision_timeout",
    "vision_retries",
    "vision_cache_limit",
)

MAX_CANDIDATES = 3
JSON_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
# GigaChat принимает картинки в этих форматах.
UPLOAD_TYPES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/tiff": "image/tiff",
    "image/bmp": "image/bmp",
}

# Ответ модели: только JSON, без пояснений.
PROMPT = """На фото товар из магазина табака и электронных сигарет.

Ниже список позиций из сверки инвентаризации. Нужно выбрать те, которым
соответствует товар на фото.

Правила:
1. Читай текст на упаковке: бренд, линейку, вкус, крепость.
2. Выбирай только из списка. Своих вариантов не добавляй.
3. Вкус и вариант товара учитывай, но бренд важнее: если совпал бренд,
   а вкус разобрать нельзя, всё равно предложи эту строку.
4. Если подходящего нет, верни пустой список candidates.
5. Количество штук не считай.

Список позиций:
{candidates}

Верни только JSON без пояснений и без разметки:
{{"text": "<весь текст, разобранный на упаковке>",
  "candidates": [{{"row": <номер строки>, "confidence": <от 0 до 1>}}],
  "comment": "<кратко, если фото нечитаемое>"}}
"""


@dataclass
class Candidate:
    """Строка сверки, предлагаемая человеку для подтверждения."""

    row: int
    name: str
    diff: float
    price: float
    # Уверенность модели, приведённая к 0…1.
    confidence: float = 0.0
    # Схожесть имени строки с текстом, прочитанным на упаковке.
    ratio: float = 0.0
    # Как получена строка: `модель` или `текст` (подбор по тексту).
    source: str = "модель"


@dataclass
class PhotoResult:
    """Разбор одного снимка."""

    photo: str
    digest: str = ""
    text: str = ""
    comment: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""
    cached: bool = False
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


# --- Настройки из интерфейса -------------------------------------------------


def _number(key: str, value: object) -> object:
    """Приводит значение к типу настройки. Мусор — значение по умолчанию."""
    default = DEFAULTS[key]
    text = str(value).strip().replace(",", ".")
    try:
        return float(text) if key in FLOAT_KEYS else int(float(text))
    except (TypeError, ValueError):
        return default


def load_config(settings) -> dict:
    """Настройки распознавания: значения по умолчанию плюс правки из формы."""
    stored = runtime.load(runtime.runtime_path(settings))
    config = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in stored:
            continue
        value = stored[key]
        if key in BOOL_KEYS:
            config[key] = bool(value)
        elif key in INT_KEYS or key in FLOAT_KEYS:
            config[key] = _number(key, value)
        else:
            config[key] = str(value or "").strip()
    if config["vision_scope"] not in SCOPES:
        config["vision_scope"] = SCOPES[0]
    return config


def save_config(settings, values: dict) -> dict:
    """Пишет настройки в файл переключателей. Чужие ключи не трогаются.

    Пустой ключ авторизации означает «оставить как было»: форма не показывает
    ключ целиком, поэтому пустое поле не должно его стирать.
    """
    path = runtime.runtime_path(settings)
    stored = runtime.load(path)
    for key, value in values.items():
        if key not in DEFAULTS:
            continue
        if key in BOOL_KEYS:
            stored[key] = bool(value)
        elif key in INT_KEYS or key in FLOAT_KEYS:
            stored[key] = _number(key, value)
        elif key == "vision_api_key":
            text = str(value or "").strip()
            if text:
                stored[key] = text
        else:
            stored[key] = str(value or "").strip()
    runtime.save(stored, path)
    return load_config(settings)


def forget_key(settings) -> dict:
    """Убирает ключ авторизации из файла настроек."""
    path = runtime.runtime_path(settings)
    stored = runtime.load(path)
    stored["vision_api_key"] = ""
    runtime.save(stored, path)
    return load_config(settings)


def mask_key(key: str) -> str:
    """Ключ для показа на странице: видно только хвост."""
    text = str(key or "").strip()
    if not text:
        return ""
    return f"…{text[-4:]}" if len(text) > 4 else "…"


def _remember_scope(settings, scope: str) -> None:
    """Запоминает версию API, которая приняла ключ."""
    if scope not in SCOPES:
        return
    path = runtime.runtime_path(settings)
    stored = runtime.load(path)
    if stored.get("vision_scope") == scope:
        return
    stored["vision_scope"] = scope
    runtime.save(stored, path)
    logger.info("Версия API GigaChat: %s", scope)


def _data_dir(settings) -> Path:
    """Папка данных: та же, где база примеров и файл переключателей."""
    return Path(runtime.runtime_path(settings)).parent


def cache_path(settings) -> Path:
    return _data_dir(settings) / "vision-cache.json"


def samples_path(settings) -> Path:
    return _data_dir(settings) / "vision-samples.jsonl"


def status(settings, config: dict | None = None) -> dict:
    """Состояние распознавания для страницы интерфейса."""
    config = config or load_config(settings)
    enabled = bool(config["vision_enabled"])
    has_key = bool(config["vision_api_key"])
    model = str(config["vision_model"])

    if not enabled:
        reason = "Выключено: фото товара разбирает человек."
    elif not has_key:
        reason = (
            "Включено, но ключ авторизации GigaChat не введён. "
            "Введите его в настройках ниже."
        )
    else:
        reason = f"Включено, модель {model}."

    cache = cache_path(settings)
    try:
        stored = len(json.loads(cache.read_text(encoding="utf-8"))) if cache.is_file() else 0
    except (OSError, ValueError, TypeError):
        stored = 0

    return {
        "enabled": enabled,
        "ready": enabled and has_key,
        "has_key": has_key,
        "key_tail": mask_key(config["vision_api_key"]),
        "model": model,
        "scope": str(config["vision_scope"]),
        "reason": reason,
        "cached": stored,
        "cache_path": str(cache),
        "samples": _count_samples(settings),
    }


# --- Кандидаты ---------------------------------------------------------------


def surplus_items(items: list[resort.Item]) -> list[resort.Item]:
    """Кандидаты для фото: только излишки, самые крупные первыми."""
    plus = [item for item in items if item.diff > 0]
    plus.sort(key=lambda item: (-item.diff, item.row))
    return plus


def items_from_rows(rows: list[dict], type_words: tuple[str, ...]) -> list[resort.Item]:
    """Строки отчёта страницы результата в позиции для подбора.

    Отчёт по гроздям (`report.cluster_rows`) уже содержит номер строки, имя,
    количество и сумму — этого хватает, чтобы собрать те же `resort.Item`,
    с которыми работает подбор пересортов.
    """
    items: list[resort.Item] = []
    seen: set[int] = set()
    for row in rows or []:
        try:
            number = int(row.get("row"))
            diff = float(row.get("diff") or 0)
        except (TypeError, ValueError):
            continue
        if number in seen or not diff:
            continue
        seen.add(number)
        items.append(
            resort.make_item(
                number,
                str(row.get("name") or ""),
                diff,
                float(row.get("sum_before") or 0),
                type_words,
            )
        )
    return items


def _candidate_lines(items: list[resort.Item]) -> str:
    return "\n".join(
        f"{item.row}. {item.name} — {item.diff:g} шт., цена {item.price:.2f}"
        for item in items
    )


def _digest(image: bytes, items: list[resort.Item], model: str) -> str:
    """Ключ кеша: снимок, набор кандидатов и модель."""
    payload = hashlib.sha256(image)
    payload.update(model.encode("utf-8"))
    for item in items:
        payload.update(f"|{item.row}:{item.key}".encode("utf-8"))
    return payload.hexdigest()


def _strip_fence(text: str) -> str:
    """Модель иногда оборачивает JSON в ```json … ``` вопреки просьбе."""
    return JSON_FENCE.sub("", text or "").strip()


def _read_json(text: str) -> dict:
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(cleaned[start : end + 1])
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


class Cache:
    """Кеш ответов по хешу файла: одно фото не уходит в API дважды."""

    def __init__(self, path: str | Path, limit: int = 2000) -> None:
        self.path = Path(path)
        self.limit = max(int(limit), 1)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Не удалось прочитать кеш %s", self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, digest: str) -> dict | None:
        value = self._data.get(digest)
        return value if isinstance(value, dict) else None

    def put(self, digest: str, value: dict) -> None:
        if len(self._data) >= self.limit:
            # Кеш вспомогательный: при переполнении проще начать заново.
            self._data.clear()
        self._data[digest] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            logger.warning("Не удалось записать кеш %s", self.path)


def clear_cache(settings) -> int:
    """Удаляет кеш ответов. Возвращает число забытых снимков."""
    path = cache_path(settings)
    if not path.is_file():
        return 0
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        count = len(stored) if isinstance(stored, dict) else 0
    except (OSError, ValueError):
        count = 0
    path.unlink(missing_ok=True)
    return count


# --- Клиент модели -----------------------------------------------------------


class GigaChatVision:
    """Клиент GigaChat: токен доступа, загрузка снимка, запрос на генерацию.

    Исключений наружу не отдаёт: каждый метод возвращает пару
    «результат, описание ошибки».
    """

    def __init__(
        self,
        auth_key: str,
        model: str = "GigaChat-2",
        timeout: int = 60,
        retries: int = 2,
        scope: str = SCOPES[0],
        verify_ssl: bool = False,
    ) -> None:
        self.auth_key = auth_key
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.scope = scope if scope in SCOPES else SCOPES[0]
        self.verify_ssl = verify_ssl
        # Токен живёт 30 минут; держим его в объекте клиента.
        self._token = ""
        self._until = 0.0

    # -- сеть --

    def _context(self) -> ssl.SSLContext:
        if self.verify_ssl:
            return ssl.create_default_context()
        # У Сбера цепочка Минцифры, которой в системном хранилище обычно нет.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _open(self, request: urllib.request.Request) -> tuple[bytes, str, int]:
        """Один запрос без повторов: тело ответа, ошибка, код."""
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self._context()
            ) as answer:
                return answer.read(), "", int(answer.status or 200)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            return b"", f"GigaChat ответил ошибкой {error.code}: {detail}", error.code
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            return b"", f"Нет связи с GigaChat: {error}", 0

    def _repeat(self, build) -> tuple[bytes, str]:
        """Повторяет запрос при 429 и сбоях службы."""
        delay = 2.0
        last = ""
        for attempt in range(self.retries + 1):
            body, error, code = self._open(build())
            if not error:
                return body, ""
            last = error
            if code not in (429, 500, 502, 503, 504, 0) or attempt == self.retries:
                return b"", last
            time.sleep(delay)
            delay *= 2
        return b"", last

    # -- авторизация --

    def _ask_token(self, scope: str) -> tuple[str, str]:
        def build() -> urllib.request.Request:
            return urllib.request.Request(
                OAUTH_URL,
                data=urllib.parse.urlencode({"scope": scope}).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "RqUID": str(uuid.uuid4()),
                    "Authorization": f"Basic {self.auth_key}",
                },
            )

        body, error = self._repeat(build)
        if error:
            return "", error
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            return "", "Ответ службы авторизации не разобран."
        token = str(data.get("access_token") or "")
        if not token:
            return "", "Служба авторизации не выдала токен доступа."
        # Срок приходит метками времени в миллисекундах.
        try:
            self._until = float(data.get("expires_at", 0)) / 1000.0
        except (TypeError, ValueError):
            self._until = 0.0
        if self._until <= 0:
            self._until = time.time() + 25 * 60
        return token, ""

    def token(self) -> tuple[str, str]:
        """Токен доступа. Версию API подбирает сама, если ключ не от неё."""
        if not self.auth_key:
            return "", "Ключ авторизации GigaChat не введён."
        # Обновляем с запасом: токен живёт 30 минут.
        if self._token and time.time() < self._until - 300:
            return self._token, ""

        order = [self.scope] + [scope for scope in SCOPES if scope != self.scope]
        last = ""
        for scope in order:
            token, error = self._ask_token(scope)
            if token:
                self._token = token
                self.scope = scope
                return token, ""
            last = error
            # Ключ выдан под другую версию API — пробуем следующую.
            if "scope" not in error and " 400" not in error and " 401" not in error:
                break
        return "", last

    # -- запросы --

    def _upload(self, image: bytes, mime: str, name: str) -> tuple[str, str]:
        """Кладёт снимок в хранилище файлов. Возвращает идентификатор."""
        token, error = self.token()
        if error:
            return "", error

        boundary = f"----excelkro{uuid.uuid4().hex}"
        line = f"--{boundary}\r\n".encode("utf-8")
        parts = [
            line,
            b'Content-Disposition: form-data; name="purpose"\r\n\r\ngeneral\r\n',
            line,
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(
                "utf-8"
            ),
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
            image,
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
        body = b"".join(parts)

        def build() -> urllib.request.Request:
            return urllib.request.Request(
                f"{BASE_URL}/files",
                data=body,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )

        answer, error = self._repeat(build)
        if error:
            return "", error
        try:
            data = json.loads(answer.decode("utf-8"))
        except ValueError:
            return "", "Ответ хранилища файлов не разобран."
        file_id = str(data.get("id") or "")
        return (file_id, "") if file_id else ("", "Снимок не принят хранилищем.")

    def _forget_file(self, file_id: str) -> None:
        """Убирает снимок из хранилища. Неудача здесь ничего не ломает."""
        token, error = self.token()
        if error or not file_id:
            return
        request = urllib.request.Request(
            f"{BASE_URL}/files/{file_id}/delete",
            data=b"",
            method="POST",
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        self._open(request)

    def _chat(self, payload: dict) -> tuple[str, str]:
        token, error = self.token()
        if error:
            return "", error
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def build() -> urllib.request.Request:
            return urllib.request.Request(
                f"{BASE_URL}/chat/completions",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )

        answer, error = self._repeat(build)
        if error:
            return "", error
        try:
            data = json.loads(answer.decode("utf-8"))
        except ValueError:
            return "", "Ответ GigaChat не разобран."
        return _first_text(data), ""

    def ask(self, image: bytes, mime: str, prompt: str) -> tuple[str, str]:
        """Загружает снимок и спрашивает модель. Снимок затем удаляется."""
        upload_mime = UPLOAD_TYPES.get(mime, "image/jpeg")
        file_id, error = self._upload(image, upload_mime, "photo")
        if error:
            return "", error
        try:
            return self._chat(
                {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "attachments": [file_id],
                        }
                    ],
                    # Ответ должен быть повторяемым: температура минимальная.
                    "temperature": 0.1,
                    "max_tokens": 800,
                }
            )
        finally:
            self._forget_file(file_id)

    def ping(self) -> tuple[str, str]:
        """Проверка ключа и модели: короткий запрос без картинки."""
        return self._chat(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "Ответь одним словом: готово"}],
                "temperature": 0.1,
                "max_tokens": 16,
            }
        )


def _first_text(data: dict) -> str:
    """Достаёт текст первого ответа. Пустой ответ — пустая строка."""
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        text = (choice.get("message") or {}).get("content")
        if text:
            return str(text)
    return ""


def _client(config: dict, retries: int | None = None) -> GigaChatVision:
    return GigaChatVision(
        auth_key=str(config["vision_api_key"]),
        model=str(config["vision_model"]),
        timeout=int(config["vision_timeout"]),
        retries=int(config["vision_retries"]) if retries is None else retries,
        scope=str(config["vision_scope"]),
        verify_ssl=bool(config["vision_verify_ssl"]),
    )


def check(settings, config: dict | None = None) -> tuple[bool, str]:
    """Проверяет связь с моделью по кнопке на странице."""
    config = config or load_config(settings)
    if not config["vision_api_key"]:
        return False, "Ключ авторизации GigaChat не введён."
    client = _client(config, retries=0)
    text, error = client.ping()
    _remember_scope(settings, client.scope)
    if error:
        return False, error
    return True, (
        f"Модель {config['vision_model']} отвечает: {text.strip() or 'пусто'} "
        f"(версия API {client.scope})"
    )


# --- Разбор ответа -----------------------------------------------------------


def _by_text(
    text: str, items: list[resort.Item], type_words: tuple[str, ...], floor: float
) -> list[Candidate]:
    """Запасной подбор: сравнение прочитанного текста с именами строк.

    Работает, когда модель вернула номер строки не из списка или не вернула
    номеров вовсе, но текст с упаковки прочитала.
    """
    key = resort.normalize(text, type_words)
    if not key:
        return []
    scored: list[tuple[float, resort.Item]] = []
    for item in items:
        ratio = max(
            resort.similarity(key, item.key),
            # Имя строки короче прочитанного текста: ищем вхождение бренда.
            resort.similarity(key[: len(item.key)] if item.key else "", item.key),
        )
        if ratio >= floor:
            scored.append((ratio, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].row))
    return [
        Candidate(
            row=item.row,
            name=item.name,
            diff=item.diff,
            price=item.price,
            confidence=0.0,
            ratio=round(ratio, 3),
            source="текст",
        )
        for ratio, item in scored[:MAX_CANDIDATES]
    ]


def _build(
    answer: dict,
    items: list[resort.Item],
    type_words: tuple[str, ...],
    floor: float,
) -> tuple[list[Candidate], str, str]:
    """Сверяет ответ модели со списком кандидатов."""
    text = str(answer.get("text") or "").strip()
    comment = str(answer.get("comment") or "").strip()
    by_row = {item.row: item for item in items}
    key = resort.normalize(text, type_words)

    chosen: list[Candidate] = []
    seen: set[int] = set()
    for entry in answer.get("candidates") or []:
        if not isinstance(entry, dict):
            continue
        try:
            row = int(entry.get("row"))
        except (TypeError, ValueError):
            continue
        item = by_row.get(row)
        # Строку не из списка модель предлагать не имеет права.
        if item is None or row in seen:
            continue
        seen.add(row)
        try:
            confidence = min(1.0, max(0.0, float(entry.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        chosen.append(
            Candidate(
                row=row,
                name=item.name,
                diff=item.diff,
                price=item.price,
                confidence=round(confidence, 3),
                ratio=round(resort.similarity(key, item.key), 3) if key else 0.0,
            )
        )
        if len(chosen) >= MAX_CANDIDATES:
            break

    if not chosen:
        chosen = _by_text(text, items, type_words, floor)
    return chosen, text, comment


# --- Разбор снимков ----------------------------------------------------------


def recognize(
    photo: str | Path,
    items: list[resort.Item],
    settings,
    cache: Cache | None = None,
    config: dict | None = None,
    client: GigaChatVision | None = None,
) -> PhotoResult:
    """Разбирает один снимок и предлагает строки сверки.

    `items` — строки текущей сверки; кандидатами становятся только излишки.
    Ошибки не поднимаются наверх: они возвращаются полем `error`.
    """
    config = config or load_config(settings)
    file = Path(photo)
    result = PhotoResult(photo=file.name)

    if not config["vision_enabled"]:
        result.error = "Распознавание фото выключено."
        return result
    if not config["vision_api_key"]:
        result.error = "Ключ авторизации GigaChat не введён."
        return result
    if not file.is_file():
        result.error = "Файл снимка не найден."
        return result

    limit_mb = int(config["vision_max_photo_mb"])
    if file.stat().st_size > limit_mb * 1024 * 1024:
        result.error = f"Снимок больше {limit_mb} МБ."
        return result

    plus = surplus_items(items)[: int(config["vision_max_rows"])]
    if not plus:
        result.error = "В сверке нет излишков: сравнивать не с чем."
        return result

    model = str(config["vision_model"])
    image = file.read_bytes()
    result.digest = _digest(image, plus, model)
    type_words = tuple(getattr(settings, "type_words", ()))
    floor = float(config["vision_text_min_score"])

    stored = cache.get(result.digest) if cache is not None else None
    if stored is not None:
        result.cached = True
        result.candidates, result.text, result.comment = _build(
            stored, plus, type_words, floor
        )
        return result

    mime = mimetypes.guess_type(file.name)[0] or "image/jpeg"
    client = client or _client(config)

    started = time.monotonic()
    raw, error = client.ask(image, mime, PROMPT.format(candidates=_candidate_lines(plus)))
    result.seconds = round(time.monotonic() - started, 2)
    _remember_scope(settings, client.scope)
    if error:
        logger.warning("Фото %s: %s", file.name, error)
        result.error = error
        return result

    answer = _read_json(raw)
    if not answer:
        result.error = "Ответ модели не разобран."
        return result

    if cache is not None:
        cache.put(result.digest, answer)
    result.candidates, result.text, result.comment = _build(answer, plus, type_words, floor)
    return result


def recognize_all(
    photos: list[str | Path], items: list[resort.Item], settings
) -> list[PhotoResult]:
    """Разбирает пачку снимков подряд.

    Запросы идут последовательно, одним клиентом: токен доступа получается
    один раз на всю пачку, а пауза не даёт упереться в предел обращений.
    """
    config = load_config(settings)
    cache = Cache(cache_path(settings), int(config["vision_cache_limit"]))
    pause = float(config["vision_pause_seconds"])
    client = _client(config)
    results: list[PhotoResult] = []
    for index, photo in enumerate(photos):
        if index:
            time.sleep(pause)
        results.append(recognize(photo, items, settings, cache, config, client))
    return results


# --- Ответы человека ---------------------------------------------------------


def remember(
    settings,
    photo: str,
    digest: str,
    text: str,
    row: int,
    name: str,
    picked: bool,
    source: str = "",
) -> None:
    """Кладёт ответ человека в базу примеров по фото.

    Файл построчный JSON, как база примеров пересортов: по нему потом
    видно, где модель ошибалась, и можно оценить точность на своих фото.
    """
    record = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "photo": photo,
        "digest": digest,
        "text": text,
        "row": int(row),
        "name": name,
        "picked": bool(picked),
        "source": source,
    }
    path = samples_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("Не удалось записать пример по фото в %s", path)


def _count_samples(settings) -> int:
    path = samples_path(settings)
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8") as source:
            return sum(1 for line in source if line.strip())
    except OSError:
        return 0


def is_photo(name: str) -> bool:
    """Похоже ли имя файла на снимок."""
    return Path(str(name or "")).suffix.lower() in PHOTO_SUFFIXES
