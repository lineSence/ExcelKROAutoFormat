"""Переключатели, доступные прямо в интерфейсе.

Некоторые настройки удобно менять без правки `.env` и перезапуска службы:
это эмбеддинги имён (`embed_enabled`) и все настройки распознавания фото
вместе с ключом авторизации. Значения хранятся в маленьком JSON-файле
`runtime.json` рядом с базой примеров и накладываются поверх `Settings` на
каждый запрос. Если файла нет, действует значение из `.env` или из окружения.

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

logger = logging.getLogger("excelkro.runtime")

DEFAULT_PATH = "data/runtime.json"

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
