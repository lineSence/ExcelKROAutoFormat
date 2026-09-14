"""Переключатели, доступные прямо в интерфейсе.

Некоторые настройки удобно менять без правки файлов и перезапуска службы:
это эмбеддинги имён, LLM-судья (вместе с провайдером, ключом и
моделью) и все настройки распознавания фото. Значения хранятся в
маленьком JSON-файле `runtime.json` рядом с базой примеров и накладываются
поверх `Settings` на каждый запрос. Если файла нет, действует значение
из окружения. Ключи сервисов из окружения не читаются вовсе: их задают
только в интерфейсе.

Папка для записи выбирается сама. Служба запущена с `ProtectSystem=strict`, и
папка с кодом (`/opt/excelkro`) доступна только для чтения: попытка записать
`data/runtime.json` рядом с кодом заканчивалась ошибкой доступа и сообщением
«Настройки не сохранены». Поэтому если желаемая папка не пишется, берётся
папка состояния службы (`StateDirectory`, обычно `/var/lib/excelkro`), а в
последнюю очередь — временная папка. Выбор пишется в журнал службы.
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

# Настройки эмбеддингов, которые задаются в интерфейсе, и их типы.
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

# Настройки LLM-судьи (OpenRouter). Вес логики здесь — значение по
# умолчанию; на странице загрузки его можно задать на один файл.
JUDGE_FIELDS: dict[str, type] = {
    "logic_weight": float,
    "judge_api_key": str,
    "judge_model": str,
    "judge_api_url": str,
    "judge_timeout": float,
    "judge_retries": int,
    "judge_max_requests": int,
    "judge_batch": int,
    "judge_max_pairs": int,
}

# Всё, что можно хранить в runtime.json.
FIELDS: dict[str, type] = {**EMBED_FIELDS, **JUDGE_FIELDS}

# Выбранная папка на время жизни процесса: проверять запись на каждый
# запрос незачем.
_CHOSEN: dict[str, Path] = {}


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


def _writable(folder: Path) -> bool:
    """Можно ли писать в папку. Проверка настоящей записью, а не правами."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _spares() -> list[Path]:
    """Запасные папки: сначала состояние службы, потом временная папка."""
    spares: list[Path] = []
    for part in os.environ.get("STATE_DIRECTORY", "").split(":"):
        if part.strip():
            spares.append(Path(part.strip()) / "data")
    spares.append(Path("/var/lib/excelkro/data"))
    spares.append(Path(tempfile.gettempdir()) / "excelkro-data")
    return spares


def data_dir(settings=None) -> Path:
    """Папка данных, в которую служба действительно может писать."""
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

    # Писать некуда: отдаём желаемую папку, ошибку покажет вызывающий.
    return wanted


def runtime_path(settings) -> str:
    """Файл переключателей лежит в папке данных."""
    return str(data_dir(settings) / "runtime.json")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


def _typed(name: str, value):
    """Значение из runtime.json в том типе, который ждёт Settings."""
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
    """Накладывает переключатели интерфейса на настройки запроса."""
    values = load(runtime_path(settings))
    changes: dict[str, object] = {}
    for name in FIELDS:
        if name not in values:
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
    """Общая запись полей формы.

    Пустое текстовое поле значит «оставить как было»: так ключ не нужно
    вводить заново при каждой правке модели.
    """
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
    """Сохраняет настройки эмбеддингов из формы."""
    stored = _save_fields(settings, values, EMBED_FIELDS)
    if stored.get("embed_provider") not in embed_core.PROVIDERS:
        stored["embed_provider"] = "local"
    save(stored, runtime_path(settings))
    # Провайдер в памяти собран по старым настройкам — забываем его.
    embed_core.forget_embedder()
    return stored


def forget_embed_key(settings) -> None:
    """Удаляет ключ сервиса эмбеддингов из настроек."""
    path = runtime_path(settings)
    stored = load(path)
    stored["embed_api_key"] = ""
    save(stored, path)
    embed_core.forget_embedder()


def save_judge(settings, values: dict) -> dict:
    """Сохраняет настройки LLM-судьи из формы."""
    from . import judge as judge_core

    stored = _save_fields(settings, values, JUDGE_FIELDS)
    if "logic_weight" in stored:
        stored["logic_weight"] = judge_core.clamp_weight(stored["logic_weight"])
    save(stored, runtime_path(settings))
    # Судья в памяти собран по старым настройкам — забываем его.
    judge_core.forget_judge()
    return stored


def forget_judge_key(settings) -> None:
    """Удаляет ключ OpenRouter для LLM из настроек."""
    from . import judge as judge_core

    path = runtime_path(settings)
    stored = load(path)
    stored["judge_api_key"] = ""
    save(stored, path)
    judge_core.forget_judge()


def judge_status(settings) -> dict:
    """Состояние LLM-судьи для интерфейса."""
    from . import judge as judge_core

    return judge_core.status(settings)


def embed_status(settings) -> dict:
    """Состояние эмбеддингов: включены ли, откуда векторы и всё ли есть."""
    provider = embed_core.provider_name(settings)
    enabled = bool(settings.embed_enabled)
    key = str(getattr(settings, "embed_api_key", "") or "").strip()
    model = str(getattr(settings, "embed_model", "") or embed_core.DEFAULT_REMOTE_MODEL)

    model_file = Path(settings.embed_model_path).is_file()
    tokenizer_file = Path(settings.embed_tokenizer_path).is_file()
    library = (
        util.find_spec("onnxruntime") is not None and util.find_spec("tokenizers") is not None
    )

    if provider == "openrouter":
        ready = bool(key)
        if not enabled:
            reason = "Выключены: модель сравнивает только признаки имён, цен и количеств."
        elif ready:
            reason = (
                f"Включены: векторы берутся из OpenRouter, модель {model}. "
                "Запросы идут в интернет и тратят баланс ключа."
            )
        else:
            reason = "Включены, но не введён ключ OpenRouter: сверка идёт без эмбеддингов."
    else:
        ready = model_file and tokenizer_file and library
        if not enabled:
            reason = "Выключены: модель сравнивает только признаки имён, цен и количеств."
        elif ready:
            reason = "Включены и готовы к работе: векторы считаются на сервере."
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
        "provider": provider,
        "provider_label": embed_core.provider_label(provider),
        "providers": [
            {"value": name, "label": embed_core.provider_label(name)}
            for name in embed_core.PROVIDERS
        ],
        "model": model,
        "api_url": str(getattr(settings, "embed_api_url", "") or embed_core.OPENROUTER_URL),
        "key_tail": embed_core.mask_key(key),
        "has_key": bool(key),
        "timeout": float(getattr(settings, "embed_timeout", 20.0) or 20.0),
        "max_requests": int(getattr(settings, "embed_max_requests", 400) or 0),
        "model_file": model_file,
        "tokenizer_file": tokenizer_file,
        "library": library,
        "model_path": settings.embed_model_path,
        "tokenizer_path": settings.embed_tokenizer_path,
        "reason": reason,
    }
