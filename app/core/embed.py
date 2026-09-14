"""Третий шаг второго слоя: эмбеддинги имён товаров (опционально).

Строковая схожесть не понимает, что «Мальборо» и «Marlboro» — один бренд.
Это закрывает эмбеддинг-модель: косинус между векторами имён идёт в
классификатор одним признаком (`embed_cos`), а не отдельным решающим правилом.

Провайдера выбирает человек на странице «Модель и обучение»:

- `local` — файл ONNX на сервере (`cointegrated/rubert-tiny2` int8, ~30 МБ),
  работает без интернета. Модель готовится не на VPS:
  `python scripts/export_embed_model.py --out models`.
- `openrouter` — эмбеддинги по сети, `POST https://openrouter.ai/api/v1/embeddings`
  (формат как у OpenAI: `{"model": ..., "input": [...]}`, в ответе
  `data[].embedding`). Ключ задаётся только в интерфейсе.

Общее для обоих провайдеров:

- режим выключен по умолчанию (`EMBED_ENABLED=false`);
- векторы кэшируются на диске: номенклатура повторяется из сверки в сверку,
  ключ кэша содержит провайдера и модель, поэтому векторы разных моделей
  не перемешиваются;
- у сетевого провайдера есть предел запросов на процесс
  (`embed_max_requests`): исчерпан — сверка спокойно идёт без эмбеддингов;
- любая ошибка загрузки или запроса — молчаливый отказ, сверка не падает
  (`verify.Verifier._embed_cos` ловит исключение и работает без признака).
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("excelkro.embed")

# Провайдеры эмбеддингов и их названия для интерфейса.
PROVIDERS: tuple[str, ...] = ("local", "openrouter")
PROVIDER_LABELS: dict[str, str] = {
    "local": "На сервере (файл ONNX)",
    "openrouter": "OpenRouter (по сети)",
}

OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
DEFAULT_REMOTE_MODEL = "qwen/qwen3-embedding-0.6b"

# Сколько новых векторов копится до записи кэша на диск.
SAVE_EVERY = 200


class EmbedError(RuntimeError):
    """Вектор не получен. Сверка продолжается без эмбеддингов."""


class EmbedLimit(EmbedError):
    """Исчерпан предел сетевых запросов на процесс."""


def provider_name(settings) -> str:
    """Провайдер из настроек. Неизвестное значение — локальный файл."""
    name = str(getattr(settings, "embed_provider", "local") or "local").strip().lower()
    return name if name in PROVIDERS else "local"


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(str(name or "").strip().lower(), PROVIDER_LABELS["local"])


def mask_key(key: str) -> str:
    """Ключ показываем только хвостом: целиком его видеть незачем."""
    text = str(key or "").strip()
    if not text:
        return ""
    return "…" + text[-4:] if len(text) > 4 else "…"


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


class BaseEmbedder:
    """Общая часть провайдеров: дисковый кэш векторов и косинус имён."""

    def __init__(
        self,
        tag: str,
        cache_path: Path | None = None,
        cache_limit: int = 20000,
    ) -> None:
        self.tag = tag
        self.cache_path = cache_path
        self.cache_limit = max(100, int(cache_limit or 20000))
        self.vectors_cache: dict[str, list[float]] = {}
        self.pending = 0
        self._load_cache()

    # ——— кэш ———

    def _key(self, text: str) -> str:
        return f"{self.tag}|{text}"

    def _load_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.is_file():
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.vectors_cache = {
                str(key): [float(value) for value in values]
                for key, values in raw.items()
                if isinstance(values, list)
            }
        except (ValueError, OSError, TypeError):
            self.vectors_cache = {}

    def save_cache(self) -> None:
        if self.cache_path is None or not self.pending:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self.vectors_cache, ensure_ascii=False),
                encoding="utf-8",
            )
            self.pending = 0
        except OSError as error:
            logger.warning("Кэш эмбеддингов не сохранён: %s", error)

    # ——— векторы ———

    @staticmethod
    def _norm_text(text: str) -> str:
        return " ".join(str(text or "").lower().split())

    def _compute(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def vectors(self, texts: list[str]) -> list[list[float]]:
        """Векторы для списка имён: известное берётся из кэша, новое — разом."""
        wanted = [self._norm_text(text) for text in texts]
        missing = [
            value
            for value in dict.fromkeys(wanted)
            if value and self._key(value) not in self.vectors_cache
        ]
        if missing:
            fresh = self._compute(missing)
            if len(fresh) != len(missing):
                raise EmbedError("провайдер вернул не все векторы")
            if len(self.vectors_cache) + len(fresh) > self.cache_limit:
                # Простая защита памяти: кэш не растёт бесконечно.
                self.vectors_cache.clear()
            for value, vector in zip(missing, fresh):
                self.vectors_cache[self._key(value)] = vector
            self.pending += len(fresh)
            if self.pending >= SAVE_EVERY:
                self.save_cache()

        result: list[list[float]] = []
        for value in wanted:
            vector = self.vectors_cache.get(self._key(value))
            if vector is None:
                raise EmbedError("пустое имя товара")
            result.append(vector)
        return result

    def vector(self, text: str) -> list[float]:
        return self.vectors([text])[0]

    def cos(self, first: str, second: str) -> float:
        """Косинус между именами. Оба имени уходят в один запрос."""
        left, right = self.vectors([first, second])
        return sum(a * b for a, b in zip(left, right))


class OnnxEmbedder(BaseEmbedder):
    """Векторы имён локальной моделью ONNX через onnxruntime."""

    def __init__(
        self,
        session,
        tokenizer,
        cache_path: Path | None = None,
        cache_limit: int = 20000,
        max_length: int = 32,
        tag: str = "local",
    ) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.max_length = max_length
        self._input_names = {item.name for item in session.get_inputs()}
        super().__init__(tag=tag, cache_path=cache_path, cache_limit=cache_limit)

    def _one(self, text: str) -> list[float]:
        encoded = self.tokenizer.encode(text)
        ids = encoded.ids[: self.max_length]
        mask = [1] * len(ids)
        feeds = {
            "input_ids": [ids],
            "attention_mask": [mask],
        }
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = [[0] * len(ids)]
        feeds = {name: value for name, value in feeds.items() if name in self._input_names}

        outputs = self.session.run(None, feeds)
        states = outputs[0][0]  # (tokens, hidden)
        width = len(states[0])
        pooled = [0.0] * width
        for token in states:
            for index in range(width):
                pooled[index] += float(token[index])
        count = max(1, len(states))
        return _normalize([value / count for value in pooled])

    def _compute(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]


def _post(
    url: str,
    payload: dict,
    api_key: str,
    timeout: float,
    retries: int,
) -> dict:
    """Запрос к сервису эмбеддингов. Ошибки — понятным текстом на русском."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("X-Title", "ExcelKROAutoFormat")

    last = ""
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:300]
            except OSError:
                detail = ""
            last = f"сервис ответил {error.code}: {detail}".strip()
            # Ключ, модель и тариф повторной попыткой не исправить.
            if error.code < 500 and error.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = f"нет связи с сервисом эмбеддингов: {error}"
        except ValueError as error:
            last = f"непонятный ответ сервиса: {error}"
            break
        if attempt + 1 < attempts:
            time.sleep(1.0)
    raise EmbedError(last or "векторы не получены")


def _vectors_from(answer: dict, count: int) -> list[list[float]]:
    """Разбор ответа формата OpenAI: data[].embedding по полю index."""
    rows = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(rows, list) or len(rows) < count:
        raise EmbedError("в ответе сервиса нет векторов")
    ordered = sorted(rows, key=lambda row: int(row.get("index") or 0))
    result: list[list[float]] = []
    for row in ordered[:count]:
        values = row.get("embedding") if isinstance(row, dict) else None
        if not isinstance(values, list) or not values:
            raise EmbedError("в ответе сервиса нет векторов")
        try:
            result.append(_normalize([float(value) for value in values]))
        except (TypeError, ValueError) as error:
            raise EmbedError(f"вектор не разобран: {error}") from error
    return result


class RemoteEmbedder(BaseEmbedder):
    """Векторы имён по сети (OpenRouter и любой совместимый по формату сервис).

    `sender` подменяется в тестах: это единственное место, где идёт сеть.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_REMOTE_MODEL,
        url: str = OPENROUTER_URL,
        timeout: float = 20.0,
        retries: int = 2,
        max_requests: int = 400,
        cache_path: Path | None = None,
        cache_limit: int = 20000,
        sender=None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or DEFAULT_REMOTE_MODEL
        self.url = str(url or "").strip() or OPENROUTER_URL
        self.timeout = float(timeout or 20.0)
        self.retries = int(retries or 0)
        self.max_requests = int(max_requests or 0)
        self.sender = sender
        self.sent = 0
        super().__init__(
            tag=f"openrouter:{self.model}",
            cache_path=cache_path,
            cache_limit=cache_limit,
        )

    def _send(self, payload: dict) -> dict:
        if self.sender is not None:
            return self.sender(payload)
        return _post(self.url, payload, self.api_key, self.timeout, self.retries)

    def _compute(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise EmbedError("не задан ключ сервиса эмбеддингов")
        if self.max_requests > 0 and self.sent >= self.max_requests:
            raise EmbedLimit(
                f"предел запросов эмбеддингов исчерпан ({self.max_requests})"
            )
        self.sent += 1
        answer = self._send({"model": self.model, "input": list(texts)})
        return _vectors_from(answer, len(texts))


def remote_embedder(settings, cache: bool = True) -> RemoteEmbedder:
    """Сетевой провайдер по настройкам интерфейса."""
    cache_path = str(getattr(settings, "embed_cache_path", "") or "")
    return RemoteEmbedder(
        api_key=str(getattr(settings, "embed_api_key", "") or ""),
        model=str(getattr(settings, "embed_model", "") or DEFAULT_REMOTE_MODEL),
        url=str(getattr(settings, "embed_api_url", "") or OPENROUTER_URL),
        timeout=float(getattr(settings, "embed_timeout", 20.0) or 20.0),
        retries=int(getattr(settings, "embed_retries", 2) or 0),
        max_requests=int(getattr(settings, "embed_max_requests", 400) or 0),
        cache_path=Path(cache_path) if (cache and cache_path) else None,
        cache_limit=int(getattr(settings, "embed_cache_limit", 20000) or 20000),
    )


def check_remote(settings) -> tuple[bool, str]:
    """Проверка ключа и модели одним коротким запросом. Кэш не трогается."""
    if provider_name(settings) != "openrouter":
        return False, "Сейчас выбран локальный провайдер: проверять ключ не нужно."
    if not str(getattr(settings, "embed_api_key", "") or "").strip():
        return False, "Сначала введите ключ OpenRouter и сохраните настройки."
    embedder = remote_embedder(settings, cache=False)
    try:
        vector = embedder.vector("проверка связи")
    except EmbedError as error:
        return False, f"Проверка не прошла: {error}"
    except Exception as error:  # noqa: BLE001 — проверка не должна ронять страницу
        logger.warning("Проверка эмбеддингов не удалась: %s", error)
        return False, f"Проверка не прошла: {error}"
    return True, (
        f"Ключ работает: модель {embedder.model} вернула вектор из "
        f"{len(vector)} чисел."
    )


_EMBEDDER: BaseEmbedder | None = None
_MARKER: str | None = None


def _marker(settings) -> str:
    """Отпечаток настроек: по нему понятно, нужен ли новый провайдер."""
    provider = provider_name(settings)
    if provider == "openrouter":
        key = str(getattr(settings, "embed_api_key", "") or "")
        return "|".join(
            (
                provider,
                str(getattr(settings, "embed_model", "")),
                str(getattr(settings, "embed_api_url", "")),
                mask_key(key),
                str(bool(key)),
            )
        )
    return "|".join(
        (
            provider,
            str(getattr(settings, "embed_model_path", "")),
            str(getattr(settings, "embed_tokenizer_path", "")),
        )
    )


def _load_local(settings) -> OnnxEmbedder | None:
    model_path = Path(str(getattr(settings, "embed_model_path", "") or ""))
    tokenizer_path = Path(str(getattr(settings, "embed_tokenizer_path", "") or ""))
    if not model_path.is_file() or not tokenizer_path.is_file():
        logger.warning("Файлы эмбеддинг-модели не найдены, режим выключен.")
        return None

    try:
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as error:
        logger.warning("Нет onnxruntime/tokenizers (%s), работаем без эмбеддингов.", error)
        return None

    try:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = onnxruntime.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
    except Exception as error:  # noqa: BLE001
        logger.warning("Эмбеддинг-модель не загрузилась: %s", error)
        return None

    cache = str(getattr(settings, "embed_cache_path", "") or "")
    return OnnxEmbedder(
        session=session,
        tokenizer=tokenizer,
        cache_path=Path(cache) if cache else None,
        cache_limit=int(getattr(settings, "embed_cache_limit", 20000) or 20000),
    )


def load_embedder(settings) -> BaseEmbedder | None:
    """Ленивая загрузка одного провайдера на процесс. None — работаем без них."""
    global _EMBEDDER, _MARKER

    marker = _marker(settings)
    if _MARKER == marker:
        return _EMBEDDER
    _MARKER = marker

    if provider_name(settings) == "openrouter":
        if not str(getattr(settings, "embed_api_key", "") or "").strip():
            logger.warning("Не задан ключ OpenRouter, работаем без эмбеддингов.")
            _EMBEDDER = None
        else:
            _EMBEDDER = remote_embedder(settings)
    else:
        _EMBEDDER = _load_local(settings)
    return _EMBEDDER


def forget_embedder() -> None:
    """Сбрасывает провайдера: нужно после смены настроек в интерфейсе."""
    global _EMBEDDER, _MARKER
    if _EMBEDDER is not None:
        _EMBEDDER.save_cache()
    _EMBEDDER = None
    _MARKER = None
