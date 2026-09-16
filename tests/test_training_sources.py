"""Страница обучения: список файлов-источников показан свёрнутой таблицей.

Раньше источники выводились одной строкой через join(", "), и при десятках
файлов это была нечитаемая куча текста. Здесь проверяется только разметка
страницы: без сети и без реальных данных.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SOURCES = [
    ("Сверка образец 01.01.2026.xlsx", 440),
    ("Сверка образец 02.01.2026.xlsx", 57),
]
STATS = {
    "total": 497,
    "positives": 300,
    "negatives": 197,
    "from_decisions": 0,
    "sources": SOURCES,
}


@pytest.fixture()
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """HTML страницы /training с подставленной сводкой примеров."""
    monkeypatch.setenv("TMP_DIR", str(tmp_path / "work"))
    for module in ("app.main", "app.config"):
        sys.modules.pop(module, None)
    import app.main as web
    from app.web import render

    monkeypatch.setattr(render, "dataset_stats", lambda path: STATS)
    answer = TestClient(web.app).get("/training")
    assert answer.status_code == 200
    return answer.text


def test_sources_are_collapsed_by_default(page: str) -> None:
    assert '<details class="compact-details">' in page
    assert "<details open" not in page
    assert "Источники примеров" in page


def test_sources_are_a_table(page: str) -> None:
    for name, count in SOURCES:
        assert f"<td>{name}</td>" in page
        assert f'<td class="num">{count}</td>' in page


def test_sources_are_not_one_line_of_text(page: str) -> None:
    one_line = ", ".join(f"('{name}', {count})" for name, count in SOURCES)
    assert one_line not in page


def test_summary_shows_counts(page: str) -> None:
    assert f"файлов {len(SOURCES)}" in page
    assert f"примеров {STATS['total']}" in page
