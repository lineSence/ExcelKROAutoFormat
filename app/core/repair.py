"""Шаг 1. Ремонт файла .xlsx из 1С.

Выгрузка 1С ломается двумя группами причин.

1. Таблица текстов. В архиве нет части xl/sharedStrings.xml или она не объявлена
   в [Content_Types].xml или в xl/_rels/workbook.xml.rels.
2. Стили. Ячейки и записи стилей ссылаются на номера за пределами списков
   cellXfs, fonts, fills, borders. Именно это даёт ошибку openpyxl
   «list index out of range».

Ремонт исправляет обе группы. Стили, ширины и границы, которые в файле
корректны, сохраняются без изменений. Битые ссылки становятся нулевыми.
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
STYLES_PART = "xl/styles.xml"

SST_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' count="{count}" uniqueCount="{count}">'
)
SST_CLOSE = "</sst>"
SST_EMPTY_ITEM = '<si><t xml:space="preserve"></t></si>'

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

SHARED_CELL_PATTERN = re.compile(rb't="s"[^>]*>\s*<v>\s*(\d+)\s*</v>')
SI_PATTERN = re.compile(rb"<si[\s>]")

CELL_STYLE_PATTERN = re.compile(r'(<c\b[^>]*?\bs=")(\d+)(")')
ROW_STYLE_PATTERN = re.compile(r'(<row\b[^>]*?\bs=")(\d+)(")')
COL_STYLE_PATTERN = re.compile(r'(<col\b[^>]*?\bstyle=")(\d+)(")')


class RepairError(Exception):
    """Файл не удалось подготовить к чтению."""


# --- таблица текстов ------------------------------------------------------


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


def _sheet_parts(names: list[str]) -> list[str]:
    return [
        name
        for name in names
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    ]


def _max_shared_index(archive: zipfile.ZipFile) -> int:
    """Наибольший номер текста, на который ссылаются листы (или -1)."""
    highest = -1
    for name in _sheet_parts(archive.namelist()):
        for match in SHARED_CELL_PATTERN.finditer(archive.read(name)):
            highest = max(highest, int(match.group(1)))
    return highest


def _count_shared_items(data: bytes) -> int:
    return len(SI_PATTERN.findall(data))


def _build_shared_strings(existing: bytes | None, needed: int) -> bytes:
    """Дополняет таблицу текстов пустыми записями до нужной длины."""
    if needed <= 0:
        return existing if existing else EMPTY_SHARED_STRINGS.encode("utf-8")

    if existing:
        have = _count_shared_items(existing)
        if have >= needed:
            return existing
        text = existing.decode("utf-8", errors="replace")
        padding = SST_EMPTY_ITEM * (needed - have)
        if "</sst>" in text:
            text = text.replace("</sst>", padding + "</sst>")
        else:
            text = re.sub(r"/>\s*$", ">" + padding + SST_CLOSE, text.strip())
        text = re.sub(r'count="\d+"', f'count="{needed}"', text, count=1)
        text = re.sub(r'uniqueCount="\d+"', f'uniqueCount="{needed}"', text, count=1)
        return text.encode("utf-8")

    return (SST_OPEN.format(count=needed) + SST_EMPTY_ITEM * needed + SST_CLOSE).encode("utf-8")


# --- стили --------------------------------------------------------------


def _section(xml: str, tag: str) -> tuple[int, int] | None:
    """Границы содержимого раздела стилей."""
    open_match = re.search(rf"<{tag}\b[^>]*?(/?)>", xml)
    if open_match is None:
        return None
    if open_match.group(1) == "/":
        return open_match.end(), open_match.end()
    close = xml.find(f"</{tag}>", open_match.end())
    if close < 0:
        return None
    return open_match.end(), close


def _section_text(xml: str, tag: str) -> str:
    bounds = _section(xml, tag)
    return "" if bounds is None else xml[bounds[0] : bounds[1]]


def _count_children(xml: str, tag: str, child: str) -> int:
    return len(re.findall(rf"<{child}\b", _section_text(xml, tag)))


def _style_limits(styles_xml: str) -> dict[str, int]:
    return {
        "cellXfs": _count_children(styles_xml, "cellXfs", "xf"),
        "cellStyleXfs": _count_children(styles_xml, "cellStyleXfs", "xf"),
        "fonts": _count_children(styles_xml, "fonts", "font"),
        "fills": _count_children(styles_xml, "fills", "fill"),
        "borders": _count_children(styles_xml, "borders", "border"),
    }


def _clamp_attributes(section: str, limits: dict[str, int]) -> tuple[str, int]:
    """Обнуляет ссылки стилей за пределами списков."""
    fixes = 0
    pairs = (
        ("fontId", limits["fonts"]),
        ("fillId", limits["fills"]),
        ("borderId", limits["borders"]),
        ("xfId", limits["cellStyleXfs"]),
    )
    for attribute, limit in pairs:
        if limit <= 0:
            continue

        def replace(match: re.Match, limit: int = limit) -> str:
            nonlocal fixes
            if int(match.group(2)) < limit:
                return match.group(0)
            fixes += 1
            return f"{match.group(1)}0{match.group(3)}"

        section = re.sub(rf'({attribute}=")(\d+)(")', replace, section)
    return section, fixes


def _fix_styles(styles_xml: str) -> tuple[str, int]:
    """Исправляет битые ссылки внутри xl/styles.xml."""
    limits = _style_limits(styles_xml)
    fixes = 0
    for tag in ("cellXfs", "cellStyleXfs"):
        bounds = _section(styles_xml, tag)
        if bounds is None:
            continue
        start, end = bounds
        fixed, count = _clamp_attributes(styles_xml[start:end], limits)
        fixes += count
        styles_xml = styles_xml[:start] + fixed + styles_xml[end:]
    return styles_xml, fixes


def _fix_sheet_styles(sheet_xml: str, cell_xfs: int) -> tuple[str, int]:
    """Обнуляет номера стилей ячеек, строк и столбцов вне cellXfs."""
    if cell_xfs <= 0:
        return sheet_xml, 0
    fixes = 0

    def replace(match: re.Match) -> str:
        nonlocal fixes
        if int(match.group(2)) < cell_xfs:
            return match.group(0)
        fixes += 1
        return f"{match.group(1)}0{match.group(3)}"

    for pattern in (CELL_STYLE_PATTERN, ROW_STYLE_PATTERN, COL_STYLE_PATTERN):
        sheet_xml = pattern.sub(replace, sheet_xml)
    return sheet_xml, fixes


def _style_problems(archive: zipfile.ZipFile) -> int:
    """Считает битые ссылки стилей в архиве."""
    names = archive.namelist()
    if STYLES_PART not in names:
        return 0
    styles_xml = archive.read(STYLES_PART).decode("utf-8", errors="replace")
    _, fixes = _fix_styles(styles_xml)
    cell_xfs = _style_limits(styles_xml)["cellXfs"]
    for name in _sheet_parts(names):
        sheet_xml = archive.read(name).decode("utf-8", errors="replace")
        _, sheet_fixes = _fix_sheet_styles(sheet_xml, cell_xfs)
        fixes += sheet_fixes
    return fixes


# --- общие шаги ---------------------------------------------------------


def _declared(archive: zipfile.ZipFile, part: str) -> bool:
    if part not in archive.namelist():
        return False
    return b"sharedStrings.xml" in archive.read(part)


def needs_repair(path: str | Path) -> bool:
    """Проверяет признаки поломки выгрузки 1С."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if SHARED_STRINGS_PART not in names:
            return _max_shared_index(archive) >= 0 or _style_problems(archive) > 0
        if not _declared(archive, CONTENT_TYPES_PART):
            return True
        if not _declared(archive, WORKBOOK_RELS_PART):
            return True
        needed = _max_shared_index(archive) + 1
        if needed > _count_shared_items(archive.read(SHARED_STRINGS_PART)):
            return True
        return _style_problems(archive) > 0


def repair_by_inject(source: str | Path, target: str | Path) -> Path:
    """Пересобирает архив: таблица текстов и ссылки стилей."""
    source = Path(source)
    target = Path(target)

    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if CONTENT_TYPES_PART not in names:
            raise RepairError("В архиве нет [Content_Types].xml. Это не файл .xlsx.")
        if not needs_repair(source):
            if source != target:
                shutil.copyfile(source, target)
            return target

        existing = archive.read(SHARED_STRINGS_PART) if SHARED_STRINGS_PART in names else None
        shared_strings = _build_shared_strings(existing, _max_shared_index(archive) + 1)

        styles_xml = None
        cell_xfs = 0
        if STYLES_PART in names:
            styles_xml, _ = _fix_styles(
                archive.read(STYLES_PART).decode("utf-8", errors="replace")
            )
            cell_xfs = _style_limits(styles_xml)["cellXfs"]

        items = [
            (item, archive.read(item.filename))
            for item in archive.infolist()
            if item.filename != SHARED_STRINGS_PART
        ]

    sheet_names = set(_sheet_parts([item.filename for item, _ in items]))

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as new_archive:
        for item, data in items:
            name = item.filename
            if name == CONTENT_TYPES_PART:
                data = _add_content_type(data.decode("utf-8")).encode("utf-8")
            elif name == WORKBOOK_RELS_PART:
                data = _add_relationship(data.decode("utf-8")).encode("utf-8")
            elif name == STYLES_PART and styles_xml is not None:
                data = styles_xml.encode("utf-8")
            elif name in sheet_names and cell_xfs > 0:
                fixed, _ = _fix_sheet_styles(data.decode("utf-8", errors="replace"), cell_xfs)
                data = fixed.encode("utf-8")
            new_archive.writestr(item, data)
        new_archive.writestr(SHARED_STRINGS_PART, shared_strings)
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


def describe(path: str | Path) -> dict:
    """Короткая сводка о состоянии файла. Полезно для разбора ошибок."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        styles_xml = (
            archive.read(STYLES_PART).decode("utf-8", errors="replace")
            if STYLES_PART in names
            else ""
        )
        return {
            "has_shared_strings": SHARED_STRINGS_PART in names,
            "declared_in_content_types": _declared(archive, CONTENT_TYPES_PART),
            "declared_in_rels": _declared(archive, WORKBOOK_RELS_PART),
            "shared_items": (
                _count_shared_items(archive.read(SHARED_STRINGS_PART))
                if SHARED_STRINGS_PART in names
                else 0
            ),
            "shared_needed": _max_shared_index(archive) + 1,
            "style_limits": _style_limits(styles_xml) if styles_xml else {},
            "style_problems": _style_problems(archive),
            "sheets": len(_sheet_parts(names)),
        }
