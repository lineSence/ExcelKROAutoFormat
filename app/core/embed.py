"""Третий шаг второго слоя: эмбеддинги имён товаров (опционально).

Строковая схожесть не понимает, что «Мальборо» и «Marlboro» — один бренд.
Это закрывает лёгкая русскоязычная модель `cointegrated/rubert-tiny2`,
экспортированная в ONNX int8 (около 30 МБ). Косинус между векторами
идёт в классификатор одним из признаков, а не отдельным решающим правилом.

Ограничения сервера (1 ядро, 1 ГБ):
- режим выключен по умолчанию (`EMBED_ENABLED=false`);
- сессия однопоточная и создаётся лениво, при первом запросе;
- векторы кэшируются на диске: номенклатура повторяется из сверки в сверку;
- любая ошибка загрузки — молчаливый отказ: сверка работает без эмбеддингов.

Модель готовится не на VPS, а на машине разработчика:
`python scripts/export_embed_model.py --out models`.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger("excelkro.embed")


class OnnxEmbedder:
    """Векторы имён через onnxruntime с дисковым кэшем."""

    def __init__(
        self,
        session,
        tokenizer,
        cache_path: Path | None = None,
        cache_limit: int = 20000,
        max_length: int = 32,
    ) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.cache_path = cache_path
        self.cache_limit = cache_limit
        self.max_length = max_length
        self.vectors: dict[str, list[float]] = {}
        self.dirty = False
        self._input_names = {item.name for item in session.get_inputs()}
        self._load_cache()

    # ——— кэш ———

    def _load_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.is_file():
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.vectors = {key: list(map(float, value)) for key, value in raw.items()}
        except (ValueError, OSError):
            self.vectors = {}

    def save_cache(self) -> None:
        if self.cache_path is None or not self.dirty:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(self.vectors, ensure_ascii=False),
                encoding="utf-8",
            )
            self.dirty = False
        except OSError as error:
            logger.warning("Кэш эмбеддингов не сохранён: %s", error)

    # ——— векторы ———

    def vector(self, text: str) -> list[float]:
        key = " ".join(str(text or "").lower().split())
        cached = self.vectors.get(key)
        if cached is not None:
            return cached

        encoded = self.tokenizer.encode(key)
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
        pooled = [value / count for value in pooled]
        norm = math.sqrt(sum(value * value for value in pooled)) or 1.0
        pooled = [value / norm for value in pooled]

        if len(self.vectors) >= self.cache_limit:
            # Простая защита памяти: кэш не растёт бесконечно.
            self.vectors.clear()
        self.vectors[key] = pooled
        self.dirty = True
        return pooled

    def cos(self, first: str, second: str) -> float:
        left, right = self.vector(first), self.vector(second)
        return sum(a * b for a, b in zip(left, right))


_EMBEDDER: OnnxEmbedder | None = None
_TRIED: str | None = None


def load_embedder(settings) -> OnnxEmbedder | None:
    """Ленивая загрузка одного экземпляра на процесс. None — работаем без них."""
    global _EMBEDDER, _TRIED

    model_path = Path(str(getattr(settings, "embed_model_path", "") or ""))
    tokenizer_path = Path(str(getattr(settings, "embed_tokenizer_path", "") or ""))
    marker = f"{model_path}|{tokenizer_path}"
    if _EMBEDDER is not None and _TRIED == marker:
        return _EMBEDDER
    if _TRIED == marker:
        return None
    _TRIED = marker

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

    cache = getattr(settings, "embed_cache_path", "") or ""
    _EMBEDDER = OnnxEmbedder(
        session=session,
        tokenizer=tokenizer,
        cache_path=Path(cache) if cache else None,
        cache_limit=int(getattr(settings, "embed_cache_limit", 20000)),
    )
    return _EMBEDDER
