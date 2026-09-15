"""Переключатели интерфейса и их постоянное хранилище.

Этот модуль отделяет пользовательские настройки от базового ``Settings``.
Значения, введённые через web-интерфейс, живут в runtime.json и применяются
при каждом построении рабочего экземпляра настроек.
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
# Все постоянные пользовательские параметры должны жить вне каталога кода.
DEFAULT_PATH = "/var/lib/excelkro/data/runtime.json"
# Внутренний кэш выбранного каталога нужен, чтобы не проверять права на запись
# при каждом запросе. Значением может быть стандартный каталог или fallback.
_CHOSEN: dict[str, Path] = {}

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
    # verify_mode и logic_weight задают поведение новых сверок.
    "verify_mode": str,
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
    # При true неточные значения из справочников записываются автоматически.
    "refs_auto_confirm": bool,
}

FIELDS: dict[str, type] = {**EMBED_FIELDS, **JUDGE_FIELDS}


def load(path: str | Path = DEFAULT_PATH) -> dict:
    """Прочитать JSON настроек; битый/отсутствующий файл означает «нет настроек»."""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Не удалось прочитать %s", file)
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(file: Path, text: str) -> None:
    """Атомарно заменить файл и не оставить частично записанный JSON."""
    file.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        fd, name = tempfile.mkstemp(prefix=f".{file.name}.", dir=file.parent)
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            # В runtime.json могут находиться ключи API, поэтому права файла
            # всегда ограничены владельцем независимо от umask каталога.
            os.chmod(temp, 0o600)
            os.replace(temp, file)
        finally:
            temp.unlink(missing_ok=True)
    finally:
        os.umask(old_umask)


def save(values: dict, path: str | Path = DEFAULT_PATH) -> None:
    """Сохранить полный набор runtime-настроек через атомарную замену."""
    file = Path(path)
    _atomic_write(file, json.dumps(values, ensure_ascii=False, indent=2))


def set_flag(name: str, value: bool, path: str | Path = DEFAULT_PATH) -> dict:
    """Изменить один булевый переключатель, не затрагивая остальные значения."""
    values = load(path)
    values[name] = bool(value)
    save(values, path)
    return values


def _writable(path: Path) -> bool:
    """Проверить реальную запись, а не формальный `os.access()`."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def data_dir(settings=None) -> Path:
    """Выбрать единый каталог постоянных данных приложения."""
    configured = str(getattr(settings, "train_store_path", "") or "")
    path = Path(configured) if configured else Path(DEFAULT_PATH)
    if not path.is_absolute():
        path = Path("/var/lib/excelkro/data") / path.name
    folder = path.parent
    key = str(folder)
    if key in _CHOSEN:
        return _CHOSEN[key]
    if _writable(folder):
        _CHOSEN[key] = folder
        return folder
    # Fallback нужен для CI и ограниченных контейнеров, где стандартный
    # STATE_DIRECTORY недоступен текущему пользователю.
    fallback = Path(tempfile.gettempdir()) / "excelkro-data"
    if _writable(fallback):
        logger.warning("Не удалось подготовить каталог состояния %s; используется %s", folder, fallback)
        _CHOSEN[key] = fallback
        return fallback
    logger.warning("Не удалось подготовить каталог состояния %s", folder)
    return folder


def runtime_path(settings) -> str:
    """Полный путь к runtime.json для текущей установки."""
    return str(data_dir(settings) / "runtime.json")


def _as_bool(value) -> bool:
    """Преобразовать HTML/JSON-значение в bool."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _typed(name: str, value):
    """Привести значение runtime-поля к типу, заданному в `FIELDS`."""
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
    """Наложить сохранённые runtime-настройки на базовый `Settings`.

    URL внешних провайдеров не принимаются из runtime.json, даже если такой
    ключ был записан старой версией: допустимые endpoints задаёт код.
    """
    values = load(runtime_path(settings))
    fields = getattr(settings, "__dataclass_fields__", {})
    changes: dict[str, object] = {}
    judge_provider = str(
        values.get("judge_provider") or getattr(settings, "judge_provider", "openrouter")
    ).strip().lower()
    for name in FIELDS:
        if name not in values or name not in fields:
            continue
        try:
            if name == "judge_api_url":
                changes[name] = "" if judge_provider == "gigachat" else "https://openrouter.ai/api/v1/chat/completions"
            elif name == "embed_api_url":
                changes[name] = embed_core.OPENROUTER_URL
            elif name == "verify_mode":
                value = _typed(name, values[name]).lower()
                changes[name] = value if value in {"off", "model", "llm"} else "off"
            else:
                changes[name] = _typed(name, values[name])
        except (TypeError, ValueError):
            logger.warning("Значение %s в runtime.json не понятно, берётся прежнее", name)
    return replace(settings, **changes) if changes else settings


def set_embed(settings, enabled: bool) -> None:
    """Включить/выключить эмбеддинги и сбросить кэш их рабочего объекта."""
    set_flag("embed_enabled", enabled, runtime_path(settings))
    embed_core.forget_embedder()


def _save_fields(settings, values: dict, allowed: dict[str, type]) -> dict:
    """Обновить только разрешённые поля выбранной секции runtime-настроек."""
    stored = load(runtime_path(settings))
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
    """Сохранить настройки эмбеддингов и инвалидировать их рабочий объект."""
    stored = _save_fields(settings, values, EMBED_FIELDS)
    if stored.get("embed_provider") not in embed_core.PROVIDERS:
        stored["embed_provider"] = "local"
    if stored.get("embed_provider") == "openrouter":
        stored["embed_api_url"] = embed_core.OPENROUTER_URL
    save(stored, runtime_path(settings))
    embed_core.forget_embedder()
    return stored


def forget_embed_key(settings) -> None:
    """Удалить сохранённый ключ эмбеддингов."""
    path = runtime_path(settings)
    stored = load(path)
    stored["embed_api_key"] = ""
    save(stored, path)
    embed_core.forget_embedder()


def save_judge(settings, values: dict) -> dict:
    """Сохранить параметры второго слоя, LLM-судьи и связанные флаги."""
    from . import judge as judge_core

    stored = _save_fields(settings, values, JUDGE_FIELDS)
    provider = stored.get("judge_provider")
    if provider not in judge_core.PROVIDERS:
        provider = judge_core.OPENROUTER
        stored["judge_provider"] = provider
    if provider == judge_core.OPENROUTER:
        stored["judge_api_url"] = judge_core.OPENROUTER_URL
    else:
        stored["judge_api_url"] = ""
    if "logic_weight" in stored:
        stored["logic_weight"] = judge_core.clamp_weight(stored["logic_weight"])
    save(stored, runtime_path(settings))
    judge_core.forget_judge()
    return stored


def forget_judge_key(settings) -> None:
    """Удалить сохранённый ключ LLM-судьи."""
    from . import judge as judge_core

    path = runtime_path(settings)
    stored = load(path)
    stored["judge_api_key"] = ""
    save(stored, path)
    judge_core.forget_judge
