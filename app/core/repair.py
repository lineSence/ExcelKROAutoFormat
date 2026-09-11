"""Шаг 1. Ремонт файла .xlsx из 1С.

В архиве из 1С нет части xl/sharedStrings.xml, поэтому openpyxl не открывает файл.
Основной способ ремонта: вставить пустой sharedStrings.xml и связи к нему.
Стили, ширины и границы из 1С при этом сохраняются полностью.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

SHARED_STRINGS_PART = "xl/sharedStrings.xml"
CONTENT_TYPES_PART = "[Content_Types].xml"
WORKBOOK_RELS_PART = "xl/_rels/workbook.xml.rels"

EMPTY_SHARED_STRINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' count="0" uniqueCount="0"/>'
)

CONTENT_TYPE_OVERRIDE = (
    '<Override PartName="/xl/sharedStrings.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.'
    'spreadsheetml.sharedStrings+xml"/>'
)

RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
)


class RepairError(Exception):
    """Файл не удалось подготовить к чтению."""


def _free_relationship_id(rels_xml: str) -> str:
    numbers = [int(value) for value in re.findall(r'Id="rId(\d+)"', rels_xml)]
    return f"rId{max(numbers) + 1 if numbers else 1}"


def _add_content_type(xml: str) -> str:
    if "sharedStrings.xml" in xml:
        return xml
    return xml.replace("</Types>", CONTENT_TYPE_OVERRIDE + "</Types>")


def _add_relationship(xml: str) -> str:
    if "sharedStrings.xml" in xml:
        return xml
    rel_id = _free_relationship_id(xml)
    relationship = (
        f'<Relationship Id="{rel_id}" Type="{RELATIONSHIP_TYPE}"'
        ' Target="sharedStrings.xml"/>'
    )
    return xml.replace("</Relationships>", relationship + "</Relationships>")


def needs_repair(path: str | Path) -> bool:
    """Проверяет, нет ли в архиве части sharedStrings.xml."""
    with zipfile.ZipFile(path) as archive:
        return SHARED_STRINGS_PART not in archive.namelist()


def repair_by_inject(source: str | Path, target: str | Path) -> Path:
    """Пересобирает архив с пустым sharedStrings.xml."""
    source = Path(source)
    target = Path(target)
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if SHARED_STRINGS_PART in names:
            if source != target:
                shutil.copyfile(source, target)
            return target
        if CONTENT_TYPES_PART not in names:
            raise RepairError("В архиве нет [Content_Types].xml. Это не файл .xlsx.")
        items = [(item, archive.read(item.filename)) for item in archive.infolist()]

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new_archive:
        for item, data in items:
            if item.filename == CONTENT_TYPES_PART:
                data = _add_content_type(data.decode("utf-8")).encode("utf-8")
            elif item.filename == WORKBOOK_RELS_PART:
                data = _add_relationship(data.decode("utf-8")).encode("utf-8")
            new_archive.writestr(item, data)
        new_archive.writestr(SHARED_STRINGS_PART, EMPTY_SHARED_STRINGS)
    return target


def repair_by_libreoffice(source: str | Path, target_dir: str | Path) -> Path:
    """Запасной способ. Меняет стили и ширины, поэтому не основной."""
    source = Path(source)
    target_dir = Path(target_dir)
    command = [
        "soffice",
        "--headless",
        "--convert-to",
        "xlsx",
        "--outdir",
        str(target_dir),
        str(source),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        raise RepairError(f"LibreOffice не смог открыть файл: {error}") from error
    result = target_dir / f"{source.stem}.xlsx"
    if not result.is_file():
        raise RepairError("LibreOffice не создал выходной файл.")
    return result


def repair(source: str | Path, target: str | Path, mode: str = "inject") -> Path:
    """Готовит файл к чтению выбранным способом."""
    if mode == "libreoffice":
        return repair_by_libreoffice(source, Path(target).parent)
    return repair_by_inject(source, target)
