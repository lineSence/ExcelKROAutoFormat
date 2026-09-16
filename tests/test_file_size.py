"""[DEV-FILE-SIZE] Предел размера отдельного файла кода.

Агент читает файлы целиком и без поиска по коду, поэтому модуль-свалка
на десятки килобайт съедает контекстное окно и молча обрезается при
перезаписи.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("app", "companion", "tests")

BYTE_LIMIT = 25_000
LINE_LIMIT = 800
BYTE_TARGET = 20_000

# Унаследованные нарушители на 2026-09-16: расти им нельзя, только
# уменьшаться. Новые записи в список не добавляются.
LEGACY: dict[str, int] = {
    "app/core/mail.py": 42_922,
    "app/core/format.py": 38_307,
    "app/core/vision.py": 37_450,
    "app/core/refs.py": 29_739,
}


def _sources() -> list[Path]:
    found: list[Path] = []
    for folder in FOLDERS:
        found.extend(sorted((ROOT / folder).rglob("*.py")))
    return found


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_no_new_oversized_files() -> None:
    """Файл вне списка LEGACY не превышает жёсткий предел."""
    broken: list[str] = []
    for path in _sources():
        name = _relative(path)
        if name in LEGACY:
            continue
        size = path.stat().st_size
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if size > BYTE_LIMIT or lines > LINE_LIMIT:
            broken.append(f"{name}: {size} байт, {lines} строк")
    assert not broken, (
        "[DEV-FILE-SIZE] файлы переросли предел "
        f"({BYTE_LIMIT} байт / {LINE_LIMIT} строк), нужно разбиение: {broken}"
    )


def test_legacy_files_do_not_grow() -> None:
    """Старые крупные модули только уменьшаются."""
    grown: list[str] = []
    for name, allowed in sorted(LEGACY.items()):
        path = ROOT / name
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > allowed:
            grown.append(f"{name}: {size} байт при пределе {allowed}")
    assert not grown, f"[DEV-FILE-SIZE] унаследованные файлы выросли: {grown}"


def test_legacy_list_has_no_stale_entries() -> None:
    """Разбитый или удалённый файл убирается из LEGACY."""
    stale: list[str] = []
    for name in sorted(LEGACY):
        path = ROOT / name
        if not path.is_file():
            stale.append(f"{name}: файла нет")
        elif path.stat().st_size <= BYTE_LIMIT:
            stale.append(f"{name}: умещается в предел")
    assert not stale, f"[DEV-FILE-SIZE] почистите список LEGACY: {stale}"


def test_rule_is_written_down() -> None:
    text = (ROOT / "docs" / "rules" / "agent-workflow.md").read_text(encoding="utf-8")
    assert "[DEV-FILE-SIZE]" in text, "правило [DEV-FILE-SIZE] не описано"


def test_target_is_below_hard_limit() -> None:
    """Целевой размер остаётся строже жёсткого предела."""
    assert BYTE_TARGET < BYTE_LIMIT
