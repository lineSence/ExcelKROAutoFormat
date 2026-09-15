from __future__ import annotations

import logging
from pathlib import Path

from ..core import learning

logger = logging.getLogger("excelkro")


def store_answers(old, answers: dict[str, bool], settings) -> int:
    """Сохраняет решения по спорным парам как обучающие примеры."""
    if not answers:
        return 0
    try:
        source = str(getattr(old, "source_name", "manual") or "manual")
        rows = list(getattr(old, "doubtful", None) or [])
        samples = learning.samples_from_decisions(rows, answers, tuple(settings.type_words), source)
        report = learning.append_samples(settings.train_store_path, samples)
        return int(report.get("added") or 0)
    except Exception:  # noqa: BLE001
        logger.exception("Примеры не сохранены")
        return 0


def read_samples(source: Path, name: str, settings) -> list:
    return learning.samples_from_manual(
        source,
        tuple(settings.type_words),
        source=name,
    )
