"""Настройки приложения. Все значения берутся из окружения или из .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TYPE_WORDS = (
    "сигареты,стики,сигариллы,табак,жидкость,"
    "картриджи,устройство,зажигалки,спички"
)
STATE_DATA_DIR = "/var/lib/excelkro/data"


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


def _state_path(name: str, default: str) -> str:
    """Постоянные данные не должны попадать в каталог с кодом приложения."""
    value = _text(name, default)
    path = Path(value)
    if not path.is_absolute():
        path = Path(STATE_DATA_DIR) / path.name
    return str(path)


@dataclass
class Settings:
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    max_upload_mb: int = 20
    max_unpacked_mb: int = 200
    tmp_dir: str = "/tmp/excelkro"
    result_ttl_minutes: int = 60
    sheet_name: str = "TDSheet"
    repair_mode: str = "inject"
    warehouse_source: str = "filename"
    similarity_threshold: float = 0.80
    doubtful_min: float = 0.70
    doubtful_max: float = 0.90
    doubtful_limit: int = 200
    price_gate_low: float = 0.95
    price_gate_high: float = 1.50
    match_min_score: float = 1.10
    type_words: tuple[str, ...] = field(default_factory=tuple)
    report_cluster_members: bool = True
    strict_resort: bool = False
    log_level: str = "INFO"

    verify_mode: str = "off"
    model_path: str = f"{STATE_DATA_DIR}/verifier.json"
    train_store_path: str = f"{STATE_DATA_DIR}/train-samples.jsonl"
    verify_reject: float = 0.05
    verify_gray_low: float = 0.35
    verify_gray_high: float = 0.55
    verify_weight: float = 0.30
    logic_weight: float = 1.0

    judge_provider: str = "openrouter"
    judge_api_key: str = ""
    judge_model: str = "openai/gpt-4o-mini"
    judge_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    judge_timeout: float = 60.0
    judge_retries: int = 1
    judge_max_requests: int = 60
    judge_batch: int = 20
    judge_max_pairs: int = 200

    embed_enabled: bool = False
    embed_provider: str = "local"
    embed_api_key: str = ""
    embed_model: str = "qwen/qwen3-embedding-0.6b"
    embed_api_url: str = "https://openrouter.ai/api/v1/embeddings"
    embed_timeout: float = 20.0
    embed_retries: int = 2
    embed_max_requests: int = 400
    embed_model_path: str = "models/rubert-tiny2-int8.onnx"
    embed_tokenizer_path: str = "models/rubert-tiny2-tokenizer.json"
    embed_cache_path: str = f"{STATE_DATA_DIR}/embed-cache.json"
    embed_cache_limit: int = 20000

    train_epochs: int = 300
    train_max_samples: int = 60000

    refs_dir: str = "/var/lib/excelkro/refs"
    refs_state_path: str = "/var/lib/excelkro/refs/state.json"
    default_checker: str = "Разумовский"
    refs_match_min_score: float = 0.80
    refs_confirm_min_score: float = 0.95
    refs_days_around: int = 3
    refs_cell_reason: str = "C6"
    refs_cell_admin: str = "L3"
    refs_cell_checker: str = ""
    refs_cell_auditors: str = "L5"
    refs_tick_seconds: int = 30

    # Внешний доступ: при APP_HOST != loopback эти реквизиты обязательны.
    web_auth_user: str = ""
    web_auth_password: str = ""

    def refs_cells(self) -> dict[str, str]:
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
            model_path=_state_path("VERIFIER_MODEL_PATH", f"{STATE_DATA_DIR}/verifier.json"),
            train_store_path=_state_path("TRAIN_STORE_PATH", f"{STATE_DATA_DIR}/train-samples.jsonl"),
            verify_reject=_number("VERIFY_REJECT", 0.05),
            verify_gray_low=_number("VERIFY_GRAY_LOW", 0.35),
            verify_gray_high=_number("VERIFY_GRAY_HIGH", 0.55),
            verify_weight=_number("VERIFY_WEIGHT", 0.30),
            logic_weight=_number("LOGIC_WEIGHT", 1.0),
            judge_provider=_text("JUDGE_PROVIDER", "openrouter").lower(),
            judge_model=_text("JUDGE_MODEL", "openai/gpt-4o-mini"),
            judge_api_url=_text("JUDGE_API_URL", "https://openrouter.ai/api/v1/chat/completions"),
            judge_timeout=_number("JUDGE_TIMEOUT", 60.0),
            judge_retries=int(_number("JUDGE_RETRIES", 1)),
            judge_max_requests=int(_number("JUDGE_MAX_REQUESTS", 60)),
            judge_batch=int(_number("JUDGE_BATCH", 20)),
            judge_max_pairs=int(_number("JUDGE_MAX_PAIRS", 200)),
            embed_enabled=_flag("EMBED_ENABLED", False),
            embed_provider=_text("EMBED_PROVIDER", "local").lower(),
            embed_model=_text("EMBED_MODEL", "qwen/qwen3-embedding-0.6b"),
            embed_api_url=_text("EMBED_API_URL", "https://openrouter.ai/api/v1/embeddings"),
            embed_timeout=_number("EMBED_TIMEOUT", 20.0),
            embed_retries=int(_number("EMBED_RETRIES", 2)),
            embed_max_requests=int(_number("EMBED_MAX_REQUESTS", 400)),
            embed_model_path=_text("EMBED_MODEL_PATH", "models/rubert-tiny2-int8.onnx"),
            embed_tokenizer_path=_text("EMBED_TOKENIZER_PATH", "models/rubert-tiny2-tokenizer.json"),
            embed_cache_path=_state_path("EMBED_CACHE_PATH", f"{STATE_DATA_DIR}/embed-cache.json"),
            embed_cache_limit=int(_number("EMBED_CACHE_LIMIT", 20000)),
            train_epochs=int(_number("TRAIN_EPOCHS", 300)),
            train_max_samples=int(_number("TRAIN_MAX_SAMPLES", 60000)),
            refs_dir=refs_dir,
            refs_state_path=_text("REFS_STATE_PATH", str(Path(refs_dir) / "state.json")),
            default_checker=_text("DEFAULT_CHECKER", "Разумовский"),
            refs_match_min_score=_number("REFS_MATCH_MIN_SCORE", 0.80),
            refs_confirm_min_score=_number("REFS_CONFIRM_MIN_SCORE", 0.95),
            refs_days_around=int(_number("REFS_DAYS_AROUND", 3)),
            refs_cell_reason=_text("REFS_CELL_REASON", "C6"),
            refs_cell_admin=_text("REFS_CELL_ADMIN", "L3"),
            refs_cell_checker=_text("REFS_CELL_CHECKER", ""),
            refs_cell_auditors=_text("REFS_CELL_AUDITORS", "L5"),
            refs_tick_seconds=int(_number("REFS_TICK_SECONDS", 30)),
            web_auth_user=_text("WEB_AUTH_USER", ""),
            web_auth_password=_text("WEB_AUTH_PASSWORD", ""),
        )
