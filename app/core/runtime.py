"""Переключатели, доступные прямо в интерфейсе.

Некоторые настройки удобно менять без правки `.env` и перезапуска службы:
сейчас это эмбеддинги имён (`embed_enabled`). Значение хранится в маленьком
JSON-файле рядом с моделью и накладывается поверх `Settings` на каждый запрос.

Если файла нет, действует значение из `.env` или из окружения.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from importlib import util
from pathlib import Path

logger = logging.getLogger("excelkro.runtime")

DEFAULT_PATH = "data/runtime.json"


def load(path: str | Path = DEFAULT_PATH) -> dict:
    """Читает переключатели. Битый или отсутствующий файл — пустой набор."""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать %s", file)
        return {}
    return data if isinstance(data, dict) else {}


def save(values: dict, path: str | Path = DEFAULT_PATH) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def set_flag(name: str, value: bool, path: str | Path = DEFAULT_PATH) -> dict:
    values = load(path)
    values[name] = bool(value)
    save(values, path)
    return values


def runtime_path(settings) -> str:
    """Файл переключателей лежит рядом с базой примеров."""
    store = Path(getattr(settings, "train_store_path", "") or DEFAULT_PATH)
    return str(store.parent / "runtime.json") if store.parent != Path("") else DEFAULT_PATH


def apply(settings):
    """Накладывает переключатели интерфейса на настройки запроса."""
    values = load(runtime_path(settings))
    if "embed_enabled" not in values:
        return settings
    return replace(settings, embed_enabled=bool(values["embed_enabled"]))


def set_embed(settings, enabled: bool) -> None:
    set_flag("embed_enabled", enabled, runtime_path(settings))


def embed_status(settings) -> dict:
    """Состояние эмбеддингов: включены ли и есть ли всё нужное для работы."""
    model_file = Path(settings.embed_model_path).is_file()
    tokenizer_file = Path(settings.embed_tokenizer_path).is_file()
    library = util.find_spec("onnxruntime") is not None and util.find_spec("tokenizers") is not None
    enabled = bool(settings.embed_enabled)
    ready = model_file and tokenizer_file and library

    if not enabled:
        reason = "Выключены: модель сравнивает только признаки имён, цен и количеств."
    elif ready:
        reason = "Включены и готовы к работе."
    elif not library:
        reason = "Включены, но не установлены пакеты: venv/bin/pip install -r requirements-ml.txt"
    elif not model_file:
        reason = (
            "Включены, но нет файла модели "
            f"{settings.embed_model_path}: venv/bin/python scripts/export_embed_model.py"
        )
    else:
        reason = f"Включены, но нет файла словаря {settings.embed_tokenizer_path}."

    return {
        "enabled": enabled,
        "ready": ready,
        "model_file": model_file,
        "tokenizer_file": tokenizer_file,
        "library": library,
        "model_path": settings.embed_model_path,
        "tokenizer_path": settings.embed_tokenizer_path,
        "reason": reason,
    }
