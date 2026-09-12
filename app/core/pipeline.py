"""Связка всех шагов: ремонт → разбор → формат → сохранение."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from ..config import Settings
from . import report
from .format import format_workbook, output_filename
from .guard import check_archive
from .parse import ParseError, warehouse_from_filename
from .repair import RepairError, repair

logger = logging.getLogger("excelkro.pipeline")


@dataclass
class PipelineResult:
    """Результат обработки одного файла."""

    output_path: Path
    output_name: str
    summary: dict
    groups: list[dict]
    clusters: list[dict]
    doubtful: list[dict]
    source_path: Path | None = None
    source_name: str = ""
    decisions: dict[str, bool] | None = None


def work_dir(settings: Settings) -> Path:
    folder = Path(settings.tmp_dir) / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def sweep(settings: Settings) -> int:
    """Удаляет рабочие папки старше срока хранения. Возвращает число папок."""
    root = Path(settings.tmp_dir)
    if not root.is_dir():
        return 0
    deadline = time.time() - max(settings.result_ttl_minutes, 1) * 60
    removed = 0
    for folder in root.iterdir():
        try:
            if not folder.is_dir() or folder.stat().st_mtime >= deadline:
                continue
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    if removed:
        logger.info("Удалено старых рабочих папок: %s", removed)
    return removed


def pick_sheet(workbook, settings: Settings):
    if settings.sheet_name in workbook.sheetnames:
        return workbook[settings.sheet_name]
    if not workbook.sheetnames:
        raise ParseError("В файле нет ни одного листа.")
    logger.warning(
        "Лист %s не найден. Взят первый лист: %s",
        settings.sheet_name,
        workbook.sheetnames[0],
    )
    return workbook[workbook.sheetnames[0]]


def load_sheet(repaired: Path, settings: Settings):
    """Открывает книгу и даёт понятное сообщение при битом файле."""
    try:
        workbook = openpyxl.load_workbook(repaired)
    except (IndexError, KeyError, ValueError) as error:
        raise RepairError(
            "В файле биты ссылки на стили или таблица текстов. "
            "Пересохраните выгрузку в Excel или включите запасной ремонт: "
            "REPAIR_MODE=libreoffice."
        ) from error
    return workbook, pick_sheet(workbook, settings)


def process(
    input_path: str | Path,
    original_filename: str,
    settings: Settings | None = None,
    decisions: dict[str, bool] | None = None,
    folder: str | Path | None = None,
) -> PipelineResult:
    """Обрабатывает файл сверки и возвращает путь к готовому файлу.

    `folder` — готовая рабочая папка. Веб-слой передаёт ту же папку, в которую
    сохранил загруженный файл, чтобы лишние папки не оставались на диске.
    """
    settings = settings or Settings.load()
    folder = Path(folder) if folder is not None else work_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)

    check_archive(input_path, settings.max_unpacked_mb)
    repaired = repair(input_path, folder / "repaired.xlsx", settings.repair_mode)

    workbook, sheet = load_sheet(repaired, settings)

    warehouse = warehouse_from_filename(original_filename)
    if not warehouse:
        raise ParseError("Из имени файла не вышло получить имя склада.")

    format_result = format_workbook(sheet, warehouse, settings, decisions)

    output_name = output_filename(warehouse, format_result.document.doc_date)
    output_path = folder / output_name
    workbook.save(output_path)

    return PipelineResult(
        output_path=output_path,
        output_name=output_name,
        summary=report.summary(format_result),
        groups=report.group_rows(format_result),
        clusters=report.cluster_rows(format_result) if settings.report_cluster_members else [],
        doubtful=report.doubtful_rows(format_result),
        source_path=Path(input_path),
        source_name=original_filename,
        decisions=dict(decisions or {}),
    )
