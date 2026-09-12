"""Проверки входного файла до разбора: размер и распаковка архива.

Файл .xlsx — это zip-архив. Без проверки сумма распакованных частей
может быть в сотни раз больше самого файла и съесть память сервера.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .repair import RepairError

# Предельное отношение распакованного размера к размеру файла.
MAX_RATIO = 200


class UploadTooLarge(Exception):
    """Загружаемый файл больше разрешённого размера."""


def check_archive(path: str | Path, max_unpacked_mb: int = 200) -> int:
    """Проверяет архив. Возвращает сумму распакованных частей в байтах."""
    file = Path(path)
    limit = max_unpacked_mb * 1024 * 1024
    try:
        with zipfile.ZipFile(file) as archive:
            total = 0
            for item in archive.infolist():
                total += item.file_size
                if total > limit:
                    raise RepairError(
                        f"Распакованный файл больше {max_unpacked_mb} МБ. "
                        "Файл не обрабатывается."
                    )
    except zipfile.BadZipFile as error:
        raise RepairError("Файл не читается как .xlsx: битый архив.") from error

    packed = max(file.stat().st_size, 1)
    if total / packed > MAX_RATIO:
        raise RepairError(
            "Архив распаковывается слишком сильно. Похоже на испорченный файл."
        )
    return total
