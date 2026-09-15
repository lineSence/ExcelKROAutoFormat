"""Распознавание фото плюсующего товара и подбор строки сверки.

Ревизоры присылают фото товара, который оказался в излишке. Задача модуля —
не узнать товар вообще, а выбрать его из короткого списка кандидатов:
строки текущей сверки, где `F > 0`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import resort, runtime

logger = logging.getLogger("excelkro.vision")
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"
SCOPES = ("GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP")
DEFAULTS: dict = {"vision_enabled": False, "vision_model": "GigaChat-2", "vision_api_key": "", "vision_scope": SCOPES[0], "vision_verify_ssl": False, "vision_max_rows": 40, "vision_max_photo_mb": 8, "vision_timeout": 60, "vision_retries": 2, "vision_pause_seconds": 4.0, "vision_text_min_score": 0.55, "vision_cache_limit": 2000}
BOOL_KEYS = ("vision_enabled", "vision_verify_ssl")
FLOAT_KEYS = ("vision_pause_seconds", "vision_text_min_score")
INT_KEYS = ("vision_max_rows", "vision_max_photo_mb", "vision_timeout", "vision_retries", "vision_cache_limit")
MAX_CANDIDATES = 3
JSON_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
UPLOAD_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/png": "image/png", "image/tiff": "image/tiff", "image/bmp": "image/bmp"}
PROMPT = """На фото товар из магазина табака и электронных сигарет.

Ниже список позиций из сверки инвентаризации. Нужно выбрать те, которым
соответствует товар на фото.

Правила:
1. Читай текст на упаковке: бренд, линейку, вкус, крепость.
2. Выбирай только из списка. Своих вариантов не добавляй.
3. Вкус и вариант товара учитывай, но бренд важнее: если совпал бренд,
   а вкус разобрать нельзя, всё равно предложи эту строку.
4. Если подходящего нет, верни пустой список candidates.
5. Количество штук не считай.

Список позиций:
{candidates}

Верни только JSON без пояснений и без разметки:
{{"text": "<весь текст, разобранный на упаковке>",
  "candidates": [{{"row": <номер строки>, "confidence": <от 0 до 1>}}],
  "comment": "<кратко, если фото нечитаемое>"}}
"""

@dataclass
class Candidate:
    row: int
    name: str
    diff: float
    price: float
    confidence: float = 0.0
    ratio: float = 0.0
    source: str = "модель"

@dataclass
class PhotoResult:
    photo: str
    digest: str = ""
    text: str = ""
    comment: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    error: str = ""
    cached: bool = False
    seconds: float = 0.0
    @property
    def ok(self) -> bool: return not self.error
    @property
    def best(self) -> Candidate | None: return self.candidates[0] if self.candidates else None

def _number(key: str, value: object) -> object:
    default = DEFAULTS[key]
    try: return float(str(value).strip().replace(",", ".")) if key in FLOAT_KEYS else int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError): return default

def load_config(settings) -> dict:
    stored = runtime.load(runtime.runtime_path(settings)); config = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in stored: continue
        value = stored[key]
        if key in BOOL_KEYS: config[key] = bool(value)
        elif key in INT_KEYS or key in FLOAT_KEYS: config[key] = _number(key, value)
        else: config[key] = str(value or "").strip()
    if config["vision_scope"] not in SCOPES: config["vision_scope"] = SCOPES[0]
    return config

def save_config(settings, values: dict) -> dict:
    path = runtime.runtime_path(settings); stored = runtime.load(path)
    for key, value in values.items():
        if key not in DEFAULTS: continue
        if key in BOOL_KEYS: stored[key] = bool(value)
        elif key in INT_KEYS or key in FLOAT_KEYS: stored[key] = _number(key, value)
        elif key == "vision_api_key":
            text = str(value or "").strip()
            if text: stored[key] = text
        else: stored[key] = str(value or "").strip()
    runtime.save(stored, path); return load_config(settings)

def forget_key(settings) -> dict:
    path = runtime.runtime_path(settings); stored = runtime.load(path); stored["vision_api_key"] = ""; runtime.save(stored, path); return load_config(settings)

def mask_key(key: str) -> str:
    text = str(key or "").strip()
    if not text: return ""
    return f"…{text[-4:]}" if len(text) >= 4 else "…"

def _remember_scope(settings, scope: str) -> None:
    if scope not in SCOPES: return
    path = runtime.runtime_path(settings); stored = runtime.load(path)
    if stored.get("vision_scope") == scope: return
    stored["vision_scope"] = scope; runtime.save(stored, path); logger.info("Версия API GigaChat: %s", scope)

def _data_dir(settings) -> Path: return Path(runtime.runtime_path(settings)).parent
def cache_path(settings) -> Path: return _data_dir(settings) / "vision-cache.json"
def samples_path(settings) -> Path: return _data_dir(settings) / "vision-samples.jsonl"

def status(settings, config: dict | None = None) -> dict:
    config = config or load_config(settings); enabled = bool(config["vision_enabled"]); has_key = bool(config["vision_api_key"]); model = str(config["vision_model"])
    if not enabled: reason = "Выключено: фото товара разбирает человек."
    elif not has_key: reason = "Включено, но ключ авторизации GigaChat не введён. Введите его в настройках ниже."
    else: reason = f"Включено, модель {model}."
    cache = cache_path(settings)
    try: stored = len(json.loads(cache.read_text(encoding="utf-8"))) if cache.is_file() else 0
    except (OSError, ValueError, TypeError): stored = 0
    return {"enabled": enabled, "ready": enabled and has_key, "has_key": has_key, "key_tail": mask_key(config["vision_api_key"]), "model": model, "scope": str(config["vision_scope"]), "reason": reason, "cached": stored, "cache_path": str(cache), "samples": _count_samples(settings)}
