"""Верхняя навигация: состав и подписи пунктов шапки ([WEB-NAV]).

Шаблон рендерится напрямую Jinja2: сеть и приложение не нужны.
"""

from __future__ import annotations

import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED = [
    ("/", "Сверки"),
    ("/vision", "Фото"),
    ("/mail", "Почта"),
    ("/training", "Модели и обучение"),
    ("/update", "Обновление"),
]


def _render() -> str:
    env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")))
    return env.get_template("base.html").render()


def test_nav_items_order_and_labels() -> None:
    """Пункты идут в заданном порядке и с короткими подписями."""
    html = _render()
    positions = []
    for href, label in EXPECTED:
        marker = f'href="{href}">{label}</a>'
        assert marker in html, f"нет пункта {label} ({href})"
        positions.append(html.index(marker))
    assert positions == sorted(positions), "порядок пунктов шапки изменился"


def test_brand_link_is_removed() -> None:
    """В шапке нет отдельной ссылки-названия службы."""
    html = _render()
    assert 'class="brand"' not in html
    assert "Складские сверки" not in html


def test_old_labels_are_gone() -> None:
    """Прежние длинные подписи не остались в шаблоне."""
    html = _render()
    for old in ("Сверка КРО", "Фото товара", "Почта ревизоров", "Модель и обучение"):
        assert old not in html, f"осталась прежняя подпись: {old}"
