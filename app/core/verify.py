"""Второй слой проверки пересортов: лёгкая локальная модель.

Слой 1 (`resort.py`) остаётся единственным источником пар-кандидатов.
Слой 2 отвечает на один вопрос по каждой предложенной паре: «это правда
один товар?» — и может пару отклонить, усилить или отправить в спорные.
Модель не умеет придумывать пары, которых нет в кандидатах.

Модель — логистическая регрессия на признаках пары. Чистый Python, вес
файла несколько килобайт, инференс за микросекунды: это важно для VPS с
1 ядром и 1 ГБ памяти. Эмбеддинги имён (`embed.py`) подключаются как ещё
один признак и по умолчанию выключены; провайдер векторов (файл ONNX на
сервере или OpenRouter по сети) выбирается в интерфейсе.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .resort import Item, covers, price_ratio, same_brand, similarity

DIGITS = re.compile(r"\d+")

FEATURE_NAMES: tuple[str, ...] = (
    "similarity",
    "first_word",
    "two_words",
    "three_words",
    "token_jaccard",
    "prefix_ratio",
    "digits_same",
    "digits_differ",
    "price_close",
    "price_ratio",
    "covers",
    "qty_equal",
    "row_close",
    "len_ratio",
    "embed_cos",
    "embed_known",
)


def _tokens(item: Item) -> list[str]:
    return [word for word in item.key.split(" ") if word]


def _prefix_ratio(first: str, second: str) -> float:
    if not first or not second:
        return 0.0
    shared = 0
    for left, right in zip(first, second):
        if left != right:
            break
        shared += 1
    return shared / max(len(first), len(second))


def _digit_set(name: str) -> frozenset[str]:
    return frozenset(DIGITS.findall(str(name or "")))


def pair_features(
    plus: Item,
    minus: Item,
    embed_cos: float | None = None,
) -> list[float]:
    """Признаки пары «излишек — недостача» в порядке FEATURE_NAMES."""
    left, right = _tokens(plus), _tokens(minus)
    shared = set(left) & set(right)
    union = set(left) | set(right)

    plus_digits, minus_digits = _digit_set(plus.name), _digit_set(minus.name)
    has_digits = bool(plus_digits or minus_digits)

    high_price = max(plus.price, minus.price)
    price_gap = abs(plus.price - minus.price) / high_price if high_price > 0 else 1.0
    ratio = price_ratio(plus, minus)

    row_gap = abs(plus.row - minus.row)
    long_name = max(len(plus.key), len(minus.key)) or 1

    return [
        similarity(plus.key, minus.key),
        1.0 if left and right and left[0] == right[0] else 0.0,
        1.0 if len(left) >= 2 and left[:2] == right[:2] else 0.0,
        1.0 if len(left) >= 3 and left[:3] == right[:3] else 0.0,
        len(shared) / len(union) if union else 0.0,
        _prefix_ratio(plus.key, minus.key),
        1.0 if has_digits and plus_digits == minus_digits else 0.0,
        1.0 if has_digits and plus_digits != minus_digits else 0.0,
        1.0 / (1.0 + price_gap),
        min(ratio, 3.0) / 3.0,
        1.0 if covers(plus, minus) else 0.0,
        1.0 if abs(plus.diff) == abs(minus.diff) else 0.0,
        1.0 / (1.0 + row_gap),
        min(len(plus.key), len(minus.key)) / long_name,
        float(embed_cos) if embed_cos is not None else 0.0,
        1.0 if embed_cos is not None else 0.0,
    ]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


@dataclass
class LogisticModel:
    """Логистическая регрессия со стандартизацией признаков."""

    weights: list[float]
    bias: float
    mean: list[float]
    scale: list[float]
    features: tuple[str, ...] = FEATURE_NAMES
    meta: dict = field(default_factory=dict)

    def predict(self, vector: list[float]) -> float:
        total = self.bias
        for index, value in enumerate(vector):
            total += self.weights[index] * (value - self.mean[index]) / self.scale[index]
        return _sigmoid(total)

    def predict_pair(self, plus: Item, minus: Item, embed_cos: float | None = None) -> float:
        return self.predict(pair_features(plus, minus, embed_cos))

    def to_json(self) -> dict:
        return {
            "format": 1,
            "features": list(self.features),
            "weights": self.weights,
            "bias": self.bias,
            "mean": self.mean,
            "scale": self.scale,
            "meta": self.meta,
        }

    def save(self, path: str | Path) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "LogisticModel | None":
        file = Path(path)
        if not file.is_file():
            return None
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
            model = cls(
                weights=[float(value) for value in raw["weights"]],
                bias=float(raw["bias"]),
                mean=[float(value) for value in raw["mean"]],
                scale=[float(value) or 1.0 for value in raw["scale"]],
                features=tuple(raw.get("features", FEATURE_NAMES)),
                meta=dict(raw.get("meta", {})),
            )
        except (ValueError, KeyError, TypeError):
            return None
        if len(model.weights) != len(FEATURE_NAMES) or tuple(model.features) != FEATURE_NAMES:
            # Набор признаков сменился: старая модель не подходит.
            return None
        return model


def train_logistic(
    vectors: list[list[float]],
    labels: list[int],
    epochs: int = 300,
    learning_rate: float = 0.5,
    l2: float = 1e-3,
) -> LogisticModel:
    """Обучение полным градиентным спуском. Классы уравновешиваются весами."""
    if not vectors:
        raise ValueError("Нет примеров для обучения.")
    width = len(vectors[0])
    count = len(vectors)

    mean = [sum(row[index] for row in vectors) / count for index in range(width)]
    scale = []
    for index in range(width):
        variance = sum((row[index] - mean[index]) ** 2 for row in vectors) / count
        scale.append(math.sqrt(variance) or 1.0)
    normed = [
        [(row[index] - mean[index]) / scale[index] for index in range(width)]
        for row in vectors
    ]

    positives = sum(1 for label in labels if label) or 1
    negatives = count - positives or 1
    weight_positive = count / (2.0 * positives)
    weight_negative = count / (2.0 * negatives)

    weights = [0.0] * width
    bias = 0.0
    for _ in range(max(1, epochs)):
        grad = [0.0] * width
        grad_bias = 0.0
        norm = 0.0
        for row, label in zip(normed, labels):
            total = bias
            for index in range(width):
                total += weights[index] * row[index]
            error = _sigmoid(total) - label
            sample_weight = weight_positive if label else weight_negative
            error *= sample_weight
            norm += sample_weight
            for index in range(width):
                grad[index] += error * row[index]
            grad_bias += error
        norm = norm or 1.0
        for index in range(width):
            step = grad[index] / norm + l2 * weights[index]
            weights[index] -= learning_rate * step
        bias -= learning_rate * grad_bias / norm

    return LogisticModel(weights=weights, bias=bias, mean=mean, scale=scale)


@dataclass
class Verdict:
    """Ответ второго слоя по одной паре."""

    prob: float
    accept: bool
    gray: bool
    bonus: float
    reason: str


class Verifier:
    """Проверка пар моделью с зоной сомнения."""

    def __init__(
        self,
        model: LogisticModel,
        embedder=None,
        reject: float = 0.35,
        gray_low: float = 0.35,
        gray_high: float = 0.65,
        weight: float = 1.0,
    ) -> None:
        self.model = model
        self.embedder = embedder
        self.reject = reject
        self.gray_low = gray_low
        self.gray_high = gray_high
        self.weight = weight

    def _embed_cos(self, plus: Item, minus: Item) -> float | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.cos(plus.name, minus.name)
        except Exception:  # noqa: BLE001 — модель не должна ломать сверку
            return None

    def check(self, plus: Item, minus: Item) -> Verdict:
        prob = self.model.predict_pair(plus, minus, self._embed_cos(plus, minus))
        if prob < self.reject:
            return Verdict(prob, False, False, 0.0, "модель: не один товар")
        gray = self.gray_low <= prob <= self.gray_high
        reason = "модель: сомнительно" if gray else "модель: один товар"
        return Verdict(prob, True, gray, self.weight * (prob - 0.5), reason)


_CACHE: dict[tuple, Verifier | None] = {}


def model_status(settings) -> dict:
    """Краткое состояние модели для интерфейса."""
    path = Path(getattr(settings, "model_path", ""))
    model = LogisticModel.load(path) if path else None
    if model is None:
        return {
            "ready": False,
            "path": str(path),
            "samples": 0,
            "trained_at": "",
            "meta": {},
        }
    meta = dict(model.meta or {})
    return {
        "ready": True,
        "path": str(path),
        "samples": int(meta.get("samples") or 0),
        "trained_at": str(meta.get("trained_at") or ""),
        "meta": meta,
    }


def load_verifier(settings) -> Verifier | None:
    """Собирает второй слой по настройкам. None означает «только слой 1»."""
    if str(getattr(settings, "verify_mode", "off")).lower() != "model":
        return None
    path = Path(getattr(settings, "model_path", ""))
    if not path or not path.is_file():
        return None
    embed_enabled = bool(getattr(settings, "embed_enabled", False))
    # В ключе кэша есть и настройки эмбеддингов: смена провайдера,
    # модели или ключа в интерфейсе должна давать новый второй слой.
    key = (
        str(path),
        path.stat().st_mtime_ns,
        embed_enabled,
        str(getattr(settings, "embed_provider", "local")),
        str(getattr(settings, "embed_model", "")),
        str(getattr(settings, "embed_api_url", "")),
        bool(str(getattr(settings, "embed_api_key", "") or "").strip()),
    )
    if key in _CACHE:
        return _CACHE[key]

    model = LogisticModel.load(path)
    if model is None:
        _CACHE[key] = None
        return None

    embedder = None
    if embed_enabled:
        from .embed import load_embedder

        embedder = load_embedder(settings)

    verifier = Verifier(
        model=model,
        embedder=embedder,
        reject=float(getattr(settings, "verify_reject", 0.35)),
        gray_low=float(getattr(settings, "verify_gray_low", 0.35)),
        gray_high=float(getattr(settings, "verify_gray_high", 0.65)),
        weight=float(getattr(settings, "verify_weight", 1.0)),
    )
    _CACHE.clear()  # На VPS держим в памяти только одну модель.
    _CACHE[key] = verifier
    return verifier
