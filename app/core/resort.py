"""Поиск пересортов внутри группы (правило docs/06-resort-rules.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

PUNCTUATION = re.compile(r"[.,\"\u00ab\u00bb\-/()]+")
SPACES = re.compile(r"\s+")
DOUBLE_LETTERS = re.compile(r"(.)\1+")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h",
    "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

DECISION_RESORT = "пересорт"
DECISION_SURPLUS = "излишек"
DECISION_SHORTAGE = "недостача"
DECISION_NONE = "без разбора"


@dataclass
class Item:
    """Строка данных с расхождением."""

    row: int
    name: str
    diff: float
    sum_diff: float
    key: str = ""
    words: tuple[str, ...] = ()


@dataclass
class Cluster:
    """Гроздь позиций одного бренда."""

    items: list[Item] = field(default_factory=list)
    decision: str = DECISION_NONE

    @property
    def total_diff(self) -> float:
        return sum(item.diff for item in self.items)

    @property
    def brand(self) -> str:
        return self.items[0].key if self.items else ""

    @property
    def has_minus(self) -> bool:
        return any(item.diff < 0 for item in self.items)

    @property
    def has_plus(self) -> bool:
        return any(item.diff > 0 for item in self.items)


@dataclass
class DoubtfulPair:
    """Пара имён в полосе сомнения."""

    first_row: int
    first_name: str
    second_row: int
    second_name: str
    ratio: float
    linked: bool


def normalize(name: str, type_words: tuple[str, ...] = ()) -> str:
    """Шаг 1 и шаг 2: нормализация и единая запись букв."""
    text = SPACES.sub(" ", str(name or "")).strip().lower()
    text = text.replace("ё", "е").replace("й", "и")
    text = PUNCTUATION.sub(" ", text)
    text = SPACES.sub(" ", text).strip()
    words = [word for word in text.split(" ") if word]
    while words and words[0] in type_words:
        words.pop(0)
    latin = ["".join(TRANSLIT.get(letter, letter) for letter in word) for word in words]
    folded = []
    for word in latin:
        word = word.replace("ph", "f").replace("y", "i").replace("c", "k")
        folded.append(DOUBLE_LETTERS.sub(r"\1", word))
    return " ".join(folded)


def similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def _linked(first: Item, second: Item, threshold: float) -> tuple[bool, float]:
    ratio = similarity(first.key, second.key)
    if len(first.words) >= 2 and first.words[:2] == second.words[:2]:
        return True, ratio
    if first.words and second.words and first.words[0] == second.words[0]:
        return ratio >= threshold, ratio
    return False, ratio


def build_clusters(
    items: list[Item],
    threshold: float,
    doubtful_min: float,
    doubtful_max: float,
) -> tuple[list[Cluster], list[DoubtfulPair]]:
    """Собирает грозди одного бренда. Связь переходная."""
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        root_first, root_second = find(first), find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    doubtful: list[DoubtfulPair] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            linked, ratio = _linked(items[i], items[j], threshold)
            if linked:
                union(i, j)
            if doubtful_min <= ratio <= doubtful_max:
                doubtful.append(
                    DoubtfulPair(
                        first_row=items[i].row,
                        first_name=items[i].name,
                        second_row=items[j].row,
                        second_name=items[j].name,
                        ratio=round(ratio, 3),
                        linked=linked,
                    )
                )

    buckets: dict[int, Cluster] = {}
    for index, item in enumerate(items):
        buckets.setdefault(find(index), Cluster()).items.append(item)
    clusters = list(buckets.values())
    for cluster in clusters:
        cluster.decision = decide(cluster)
    return clusters, doubtful


def decide(cluster: Cluster) -> str:
    """Таблица 6.5: пересорт, излишек или недостача."""
    total = cluster.total_diff
    if total < 0:
        return DECISION_SHORTAGE
    if cluster.has_minus and cluster.has_plus:
        return DECISION_RESORT
    if total > 0:
        return DECISION_SURPLUS
    return DECISION_NONE


def make_item(row: int, name: str, diff: float, sum_diff: float, type_words: tuple[str, ...]) -> Item:
    key = normalize(name, type_words)
    return Item(
        row=row,
        name=str(name or "").strip(),
        diff=float(diff),
        sum_diff=float(sum_diff or 0),
        key=key,
        words=tuple(key.split(" ")) if key else (),
    )
