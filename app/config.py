"""Настройки приложения. Все значения берутся из окружения или из .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TYPE_WORDS = (
    "сигареты,стики,сигариллы,табак,жидкость,"
    "картриджи,устройство,зажигалки,спички"
)


def load_dotenv(path: str | os.PathLike[str]) -> None:
    """Простое чтение .env. Уже заданные переменные не переписываются."""
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _text(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _number(name: str, default: float) -> float:
    try:
        return float(_text(name, str(default)).replace(",", "."))
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    return _text(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    max_upload_mb: int = 20
    # Предел суммы распакованных частей архива: защита от zip-бомбы.
    max_unpacked_mb: int = 200
    tmp_dir: str = "/tmp/excelkro"
    # Срок хранения рабочих папок и результатов, минуты.
    result_ttl_minutes: int = 60
    sheet_name: str = "TDSheet"
    repair_mode: str = "inject"
    warehouse_source: str = "filename"
    similarity_threshold: float = 0.80
    doubtful_min: float = 0.70
    doubtful_max: float = 0.90
    # Предел числа спорных пар в отчёте на странице.
    doubtful_limit: int = 200
    # Границы отношения цен для пары разных брендов.
    price_gate_low: float = 0.95
    price_gate_high: float = 1.50
    # Ниже этой оценки пара не считается пересортом.
    match_min_score: float = 1.10
    type_words: tuple[str, ...] = field(default_factory=tuple)
    report_cluster_members: bool = True
    # Разрешить только чёткие пересорты: бренд с брендом, цена не важна.
    strict_resort: bool = False
    log_level: str = "INFO"

    # --- Второй слой проверки пересортов (docs/07-ml-verifier.md) ---
    # off   — только детерминированная логика;
    # model — пары дополнительно судит локальная модель.
    verify_mode: str = "off"
    model_path: str = "data/verifier.json"
    train_store_path: str = "data/train-samples.jsonl"
    # Ниже этого шанса пара снимается. Значение маленькое намеренно:
    # модель вмешивается только там, где она уверена.
    verify_reject: float = 0.05
    # Полоса сомнения: такие пары уходят в «Спорные пересорты».
    verify_gray_low: float = 0.35
    verify_gray_high: float = 0.55
    # Сколько веса мнение модели добавляет к оценке пары.
    verify_weight: float = 0.30

    # Эмбеддинги имён: выключены по умолчанию (VPS 1 ГБ) и переключаются в интерфейсе.
    # Если пакетов или файлов модели нет, программа тихо работает без них.
    embed_enabled: bool = False
    embed_model_path: str = "models/rubert-tiny2-int8.onnx"
    embed_tokenizer_path: str = "models/rubert-tiny2-tokenizer.json"
    embed_cache_path: str = "data/embed-cache.json"
    embed_cache_limit: int = 20000

    # Обучение в интерфейсе.
    train_epochs: int = 300
    train_max_samples: int = 60000

    # --- Справочники (причина, администратор, ревизоры) ---
    # Папка местных копий книг и файл расписания.
    refs_dir: str = "/var/lib/excelkro/refs"
    refs_state_path: str = "/var/lib/excelkro/refs/state.json"
    # Проверяющий всегда один и тот же: подставляется в форму сверки,
    # а в мини-таблицу подписей попадает строкой «Проверил».
    default_checker: str = "Разумовский"
    # Порог схожести имён складов и допустимый сдвиг даты в графике.
    # На 0.90 правильные совпадения отбрасывались, поэтому порог ниже,
    # а всё неточное уходит на подтверждение человеку.
    refs_match_min_score: float = 0.80
    # Выше этой схожести значения пишутся в файл сразу, ниже — требуют
    # подтверждения на странице результата.
    refs_confirm_min_score: float = 0.95
    refs_days_around: int = 3
    # Адреса ячеек готового файла. Пустое значение — в файл не писать.
    # Проверяющий в сам файл не пишется: в образце в верхней части
    # никаких фамилий нет, он виден только в таблице подписей.
    refs_cell_reason: str = "C5"
    refs_cell_admin: str = "L2"
    refs_cell_checker: str = ""
    refs_cell_auditors: str = "L4"
    # Шаг проверки расписания копирования, секунды.
    refs_tick_seconds: int = 30

    def refs_cells(self) -> dict[str, str]:
        """Карта «поле → ячейка» для записи в готовый файл."""
        return {
            "reason": self.refs_cell_reason.strip(),
            "admin": self.refs_cell_admin.strip(),
            "checker": self.refs_cell_checker.strip(),
            "auditors": self.refs_cell_auditors.strip(),
        }

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(os.environ.get("EXCELKRO_ENV_FILE", ".env"))
        words = tuple(
            word.strip().lower()
            for word in _text("TYPE_WORDS", DEFAULT_TYPE_WORDS).split(",")
            if word.strip()
        )
        refs_dir = _text("REFS_DIR", "/var/lib/excelkro/refs")
        return cls(
            app_host=_text("APP_HOST", "127.0.0.1"),
            app_port=int(_number("APP_PORT", 8000)),
            max_upload_mb=int(_number("MAX_UPLOAD_MB", 20)),
            max_unpacked_mb=int(_number("MAX_UNPACKED_MB", 200)),
            tmp_dir=_text("TMP_DIR", "/tmp/excelkro"),
            result_ttl_minutes=int(_number("RESULT_TTL_MINUTES", 60)),
            sheet_name=_text("SHEET_NAME", "TDSheet"),
            repair_mode=_text("REPAIR_MODE", "inject").lower(),
            warehouse_source=_text("WAREHOUSE_SOURCE", "filename").lower(),
            similarity_threshold=_number("RESORT_SIMILARITY_THRESHOLD", 0.80),
            doubtful_min=_number("DOUBTFUL_MATCH_MIN", 0.70),
            doubtful_max=_number("DOUBTFUL_MATCH_MAX", 0.90),
            doubtful_limit=int(_number("DOUBTFUL_LIMIT", 200)),
            price_gate_low=_number("PRICE_GATE_LOW", 0.95),
            price_gate_high=_number("PRICE_GATE_HIGH", 1.50),
            match_min_score=_number("MATCH_MIN_SCORE", 1.10),
            type_words=words,
            report_cluster_members=_flag("REPORT_CLUSTER_MEMBERS", True),
            strict_resort=_flag("STRICT_RESORT", False),
            log_level=_text("LOG_LEVEL", "INFO"),
            verify_mode=_text("VERIFY_MODE", "off").lower(),
            model_path=_text("VERIFIER_MODEL_PATH", "data/verifier.json"),
            train_store_path=_text("TRAIN_STORE_PATH", "data/train-samples.jsonl"),
            verify_reject=_number("VERIFY_REJECT", 0.05),
            verify_gray_low=_number("VERIFY_GRAY_LOW", 0.35),
            verify_gray_high=_number("VERIFY_GRAY_HIGH", 0.55),
            verify_weight=_number("VERIFY_WEIGHT", 0.30),
            embed_enabled=_flag("EMBED_ENABLED", False),
            embed_model_path=_text("EMBED_MODEL_PATH", "models/rubert-tiny2-int8.onnx"),
            embed_tokenizer_path=_text("EMBED_TOKENIZER_PATH", "models/rubert-tiny2-tokenizer.json"),
            embed_cache_path=_text("EMBED_CACHE_PATH", "data/embed-cache.json"),
            embed_cache_limit=int(_number("EMBED_CACHE_LIMIT", 20000)),
            train_epochs=int(_number("TRAIN_EPOCHS", 300)),
            train_max_samples=int(_number("TRAIN_MAX_SAMPLES", 60000)),
            refs_dir=refs_dir,
            refs_state_path=_text("REFS_STATE_PATH", str(Path(refs_dir) / "state.json")),
            default_checker=_text("DEFAULT_CHECKER", "Разумовский"),
            refs_match_min_score=_number("REFS_MATCH_MIN_SCORE", 0.80),
            refs_confirm_min_score=_number("REFS_CONFIRM_MIN_SCORE", 0.95),
            refs_days_around=int(_number("REFS_DAYS_AROUND", 3)),
            refs_cell_reason=_text("REFS_CELL_REASON", "C5"),
            refs_cell_admin=_text("REFS_CELL_ADMIN", "L2"),
            refs_cell_checker=_text("REFS_CELL_CHECKER", ""),
            refs_cell_auditors=_text("REFS_CELL_AUDITORS", "L4"),
            refs_tick_seconds=int(_number("REFS_TICK_SECONDS", 30)),
        )
