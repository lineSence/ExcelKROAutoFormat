"""Переключатели, доступные прямо в интерфейсе.

Некоторые настройки удобно менять без правки файлов и перезапуска службы:
это эмбеддинги имён, LLM-судья и настройки распознавания фото. Значения
хранятся в маленьком JSON-файле `runtime.json` и накладываются поверх
`Settings` только для тех ключей, которые реально являются полями `Settings`.
Ключи, используемые исключительно внутренними модулями runtime/сервисов,
не должны передаваться в `dataclasses.replace()`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import replace
from importlib import util
from pathlib import Path

from . import embed as embed_core

logger = logging.getLogger("excelkro.runtime")

DEFAULT_PATH = "data/runtime.json"

EMBED_FIELDS: dict[str, type] = {
    "embed_enabled": bool,
    "embed_provider": str,
    "embed_api_key": str,
    "embed_model": str,
    "embed_api_url": str,
    "embed_timeout": float,
    "embed_retries": int,
    "embed_max_requests": int,
}

JUDGE_FIELDS: dict[str, type] = {
    "logic_weight": float,
    "judge_provider": str,
    "judge_api_key": str,
    "judge_model": str,
    "judge_api_url": str,
    "judge_timeout": float,
    "judge_retries": int,
    "judge_max_requests": int,
    "judge_batch": int,
    "judge_max_pairs": int,
}

FIELDS: dict[str, type] = {**EMBED_FIELDS, **JUDGE_FIELDS}

_CHOSEN: dict[str, Path] = {}


def load(path: str | Path = DEFAULT_PATH) -> dict:
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


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _spares() -> list[Path]:
    spares: list[Path] = []
    for part in os.environ.get("STATE_DIRECTORY", "").split(":"):
        if part.strip():
            spares.append(Path(part.strip()) / "data")
    spares.append(Path("/var/lib/excelkro/data"))
    spares.append(Path(tempfile.gettempdir()) / "excelkro-data")
    return spares


def data_dir(settings=None) -> Path:
    store = Path(getattr(settings, "train_store_path", "") or DEFAULT_PATH)
    wanted = store.parent if str(store.parent) not in ("", ".") else Path("data")
    key = str(wanted)
    chosen = _CHOSEN.get(key)
    if chosen is not None:
        return chosen
    if _writable(wanted):
        _CHOSEN[key] = wanted
        return wanted
    for spare in _spares():
        if spare == wanted or not _writable(spare):
            continue
        logger.warning(
            "Папка %s недоступна для записи, настройки хранятся в %s", wanted, spare
        )
        _CHOSEN[key] = spare
        return spare
    return wanted


def runtime_path(settings) -> str:
    return str(data_dir(settings) / "runtime.json")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _typed(name: str, value):
    kind = FIELDS[name]
    if kind is bool:
        return _as_bool(value)
    text = str(value).strip().replace(",", ".")
    if kind is int:
        return int(float(text))
    if kind is float:
        return float(text)
    return str(value).strip()


def apply(settings):
    """Накладывает runtime-переключатели только на поля Settings."""
    values = load(runtime_path(settings))
    changes: dict[str, object] = {}
    settings_fields = getattr(settings, "__dataclass_fields__", {})
    for name in FIELDS:
        if name not in values:
            continue
        if name not in settings_fields:
            # Эти ключи нужны внутренним модулям, но не являются аргументами
            # Settings.__init__(). Никогда не передаём их в dataclasses.replace.
            continue
        try:
            changes[name] = _typed(name, values[name])
        except (TypeError, ValueError):
            logger.warning("Значение %s в runtime.json не понятно, берётся прежнее", name)
    if not changes:
        return settings
    return replace(settings, **changes)


def set_embed(settings, enabled: bool) -> None:
    set_flag("embed_enabled", enabled, runtime_path(settings))
    embed_core.forget_embedder()


def _save_fields(settings, values: dict, allowed: dict[str, type]) -> dict:
    path = runtime_path(settings)
    stored = load(path)
    for name, value in values.items():
        if name not in allowed:
            continue
        if allowed[name] is bool:
            stored[name] = _as_bool(value)
            continue
        if value is None or not str(value).strip():
            continue
        try:
            stored[name] = _typed(name, value)
        except (TypeError, ValueError):
            logger.warning("Значение %s не сохранено: не число", name)
    return stored


def save_embed(settings, values: dict) -> dict:
    stored = _save_fields(settings, values, EMBED_FIELDS)
    if stored.get("embed_provider") not in embed_core.PROVIDERS:
        stored["embed_provider"] = "local"
    save(stored, runtime_path(settings))
    embed_core.forget_embedder()
    return stored


def forget_embed_key(settings) -> None:
    path = runtime_path(settings)
    stored = load(path)
    stored["embed_api_key"] = ""
    save(stored, path)
    embed_core.forget_embedder()


def save_judge(settings, values: dict) -> dict:
    from . import judge as judge_core

    stored = _save_fields(settings, values, JUDGE_FIELDS)
    if "logic_weight" in stored:
        stored["logic_weight"] = judge_core.clamp_weight(stored["logic_weight"])
    if stored.get("judge_provider") not in judge_core.PROVIDERS:
        stored["judge_provider"] = judge_core.OPENROUTER
    save(stored, runtime_path(settings))
    judge_core.forget_judge()
    return stored


def forget_judge_key(settings) -> None:
    from . import judge as judge_core

    path = runtime_path(settings)
    stored = load(path)
    stored["judge_api_key"] = ""
    save(stored, path)
    judge_core.forget_judge()
