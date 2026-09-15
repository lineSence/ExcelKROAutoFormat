"""Разбор полей форм: всё, что приходит строкой из браузера."""
from __future__ import annotations

import re

VERIFY_MODES = ("off", "model", "llm")
DEFAULT_LOGIC = 100
META_MULTI_FIELDS = ("sellers", "seller_hours", "auditors")
CLAIM_PREFIX = "claim-"
PAIR_PREFIX = "pair-"
UNSAFE_NAME = re.compile(r"[\\/\x00]+")


def mode(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    return value if value in VERIFY_MODES else "off"


def percent(raw: object, fallback: int = DEFAULT_LOGIC) -> int:
    text = str(raw if raw is not None else "").strip().replace(",", ".")
    if not text:
        return max(0, min(100, int(fallback)))
    try:
        value = int(round(float(text)))
    except (TypeError, ValueError):
        return max(0, min(100, int(fallback)))
    return max(0, min(100, value))


def is_on(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"on", "1", "true", "yes", "да"}


def whole_number(raw: object, fallback: int = 0) -> int:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return fallback
    try:
        value = int(float(text.replace(",", ".")))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def safe_name(raw: str | None) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    tail = text.rsplit("/", 1)[-1]
    cleaned = UNSAFE_NAME.sub("", tail).strip(".")
    return cleaned or "input.xlsx"
