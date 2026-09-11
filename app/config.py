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
    """Простое чтение .env. Уже заданные переменные не перепиcываются."""
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
    tmp_dir: str = "/tmp/excelkro"
    sheet_name: str = "TDSheet"
    repair_mode: str = "inject"
    warehouse_source: str = "filename"
    similarity_threshold: float = 0.80
    doubtful_min: float = 0.70
    doubtful_max: float = 0.90
    type_words: tuple[str, ...] = field(default_factory=tuple)
    report_cluster_members: bool = True
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(os.environ.get("EXCELKRO_ENV_FILE", ".env"))
        words = tuple(
            word.strip().lower()
            for word in _text("TYPE_WORDS", DEFAULT_TYPE_WORDS).split(",")
            if word.strip()
        )
        return cls(
            app_host=_text("APP_HOST", "127.0.0.1"),
            app_port=int(_number("APP_PORT", 8000)),
            max_upload_mb=int(_number("MAX_UPLOAD_MB", 20)),
            tmp_dir=_text("TMP_DIR", "/tmp/excelkro"),
            sheet_name=_text("SHEET_NAME", "TDSheet"),
            repair_mode=_text("REPAIR_MODE", "inject").lower(),
            warehouse_source=_text("WAREHOUSE_SOURCE", "filename").lower(),
            similarity_threshold=_number("RESORT_SIMILARITY_THRESHOLD", 0.80),
            doubtful_min=_number("DOUBTFUL_MATCH_MIN", 0.70),
            doubtful_max=_number("DOUBTFUL_MATCH_MAX", 0.90),
            type_words=words,
            report_cluster_members=_flag("REPORT_CLUSTER_MEMBERS", True),
            log_level=_text("LOG_LEVEL", "INFO"),
        )
