"""Проверки целостности базы знаний (docs/)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
RULES = DOCS / "rules"
INBOX = DOCS / "memory" / "inbox.md"

INBOX_LIMIT = 50
RULE_HEAD = re.compile(r"^##\s+\[([A-Z][A-Z0-9-]+)\]\s+(.+)$")
MATURITY = re.compile(r"^\s*\u0417\u0440\u0435\u043b\u043e\u0441\u0442\u044c:\s*(core|validated|draft)\b")
DOC_PATH = re.compile(r"docs/[0-9A-Za-z_\-/]+\.md")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rule_files() -> list[Path]:
    return sorted(RULES.glob("*.md"))


def test_layout_exists() -> None:
    for rel in (
        "agents_context.md",
        "docs/_routing.md",
        "docs/_index.md",
        "docs/00-agents-context.md",
        "docs/backlog.md",
        "docs/memory/inbox.md",
        "docs/memory/journal.md",
        "docs/references/architecture-map.md",
        "docs/references/known-issues.md",
    ):
        assert (ROOT / rel).is_file(), f"нет файла {rel}"


def test_l0_is_short() -> None:
    """L0 читается всегда, поэтому должен оставаться маленьким."""
    size = (ROOT / "agents_context.md").stat().st_size
    assert size <= 8000, f"agents_context.md разросся до {size} байт"


@pytest.mark.parametrize("source", ["_routing.md", "_index.md"])
def test_referenced_docs_exist(source: str) -> None:
    text = _read(DOCS / source)
    missing = [ref for ref in sorted(set(DOC_PATH.findall(text))) if not (ROOT / ref).is_file()]
    assert not missing, f"{source} ссылается на несуществующие файлы: {missing}"


def test_rule_tags_are_unique() -> None:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for path in _rule_files():
        for line in _read(path).splitlines():
            found = RULE_HEAD.match(line)
            if not found:
                continue
            tag = found.group(1)
            if tag in seen:
                duplicates.append(f"{tag}: {seen[tag]} и {path.name}")
            else:
                seen[tag] = path.name
    assert not duplicates, f"повторяющиеся теги: {duplicates}"
    assert seen, "ни одного правила не нашлось"


def test_every_rule_has_maturity() -> None:
    """После заголовка правила идёт строка зрелости."""
    broken: list[str] = []
    for path in _rule_files():
        lines = _read(path).splitlines()
        for number, line in enumerate(lines):
            if not RULE_HEAD.match(line):
                continue
            tail = [item for item in lines[number + 1 : number + 4] if item.strip()]
            if not tail or not MATURITY.match(tail[0]):
                broken.append(f"{path.name}: {line.strip()}")
    assert not broken, f"нет строки зрелости у правил: {broken}"


def test_inbox_within_limit() -> None:
    entries = [line for line in _read(INBOX).splitlines() if line.startswith("### ")]
    assert len(entries) <= INBOX_LIMIT, f"в inbox {len(entries)} записей, предел {INBOX_LIMIT}"
