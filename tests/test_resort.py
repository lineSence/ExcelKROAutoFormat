"""Тесты подбора пересортов."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.resort import (
    DECISION_RESORT,
    DECISION_SHORTAGE,
    DECISION_SURPLUS,
    build_clusters,
    make_item,
    maybe_similar,
    pair_key,
    similarity,
)

TYPE_WORDS = ("сигареты", "стики")


def _items(rows: list[tuple[int, str, float, float]]) -> list:
    return [make_item(row, name, diff, sum_diff, TYPE_WORDS) for row, name, diff, sum_diff in rows]


def test_pair_of_one_brand_is_resort() -> None:
    items = _items(
        [
            (10, "Сигареты Parliament Aqua Blue", -3, -450.0),
            (11, "Сигареты Parliament Aqua Blue КС", 3, 450.0),
        ]
    )
    clusters, _ = build_clusters(items, 0.86, 0.60, 0.85)

    assert len(clusters) == 1
    assert clusters[0].decision == DECISION_RESORT


def test_different_brands_and_prices_stay_apart() -> None:
    items = _items(
        [
            (10, "Сигареты Winston XS Blue", -2, -300.0),
            (11, "Стики Heets Amber", 1, 1000.0),
        ]
    )
    clusters, _ = build_clusters(items, 0.86, 0.60, 0.85)

    decisions = sorted(cluster.decision for cluster in clusters)
    assert decisions == sorted([DECISION_SHORTAGE, DECISION_SURPLUS])


def test_user_decision_wins_over_calculation() -> None:
    items = _items(
        [
            (10, "Сигареты Winston XS Blue", -2, -300.0),
            (11, "Стики Heets Amber", 2, 1000.0),
        ]
    )
    key = pair_key(10, 11)

    linked, _ = build_clusters(items, 0.86, 0.60, 0.85, {key: True})
    assert len(linked) == 1
    assert linked[0].decision == DECISION_RESORT

    split, _ = build_clusters(items, 0.86, 0.60, 0.85, {key: False})
    assert len(split) == 2


def test_strict_mode_ignores_price() -> None:
    """В строгом режиме пара разных брендов с близкой ценой не берётся."""
    items = _items(
        [
            (10, "Сигареты Winston XS Blue", -2, -300.0),
            (11, "Сигареты Kent Nano White", 2, 300.0),
        ]
    )

    soft, _ = build_clusters(items, 0.86, 0.60, 0.85)
    strict, _ = build_clusters(items, 0.86, 0.60, 0.85, strict_brand_only=True)

    assert len(soft) == 1
    assert len(strict) == 2


def test_price_gates_are_configurable() -> None:
    items = _items(
        [
            (10, "Сигареты Winston XS Blue", -2, -300.0),
            (11, "Сигареты Kent Nano White", 2, 300.0),
        ]
    )

    narrow, _ = build_clusters(
        items,
        0.86,
        0.60,
        0.85,
        price_gate_low=1.20,
        price_gate_high=1.30,
    )
    assert len(narrow) == 2


def test_doubtful_limit_caps_list() -> None:
    rows = []
    for index in range(12):
        rows.append((100 + index, f"Сигареты Winston XS Blue {index}", -1, -150.0))
        rows.append((200 + index, f"Сигареты Winston XS Bluu {index}", 1, 150.0))
    items = _items(rows)

    _, doubtful = build_clusters(items, 0.86, 0.10, 0.99, doubtful_limit=5)
    assert len(doubtful) <= 5


def test_maybe_similar_rejects_far_names() -> None:
    assert maybe_similar("parliament aqua blue", "parliament aqua blue ks", 0.60)
    assert not maybe_similar("winston", "heets amber selection", 0.86)
    # Быстрый отсев не теряет похожие имена.
    assert similarity("kent nano white", "kent nano white") == 1.0
