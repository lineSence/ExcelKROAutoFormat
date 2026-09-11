"""Поиск пересортов внутри группы (правило docs/06-resort-rules.md).

Правило подбора пары повторяет ручную сверку:
1. Каждый излишек ищет себе недостачу в той же группе.
2. Сначала берутся пары с равным количеством и похожим именем,
   потом пары с близкой ценой.
3. Количество в паре может не совпадать. Тогда к одной недостаче
   добавляются несколько излишков, пока количество не закроется.
4. Излишки и недостачи без пары остаются излишками и недостачами.
"""

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

    @property
    def price(self) -> float:
        return abs(self.sum_diff / self.diff) if self.diff else 0.0


@dataclass
class Cluster:
    """Гроздь позиций одной пары пересорта."""

    items: list[Item] = field(default_factory=list)
    decision: str = DECISION_NONE

    @property
    def total_diff(self) -> float:
        return sum(item.diff for item in self.items)

    @property
    def total_sum(self) -> float:
        return sum(item.sum_diff for item in self.items)

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


def _price_bonus(first: Item, second: Item) -> float:
    """Награда за близкую цену. Ручная сверка так и делает."""
    high = max(first.price, second.price)
    if high <= 0:
        return 0.0
    gap = abs(first.price - second.price) / high
    if gap <= 0.001:
        return 0.40
    if gap <= 0.10:
        return 0.25
    if gap <= 0.25:
        return 0.10
    return 0.0


def _name_bonus(first: Item, second: Item) -> float:
    """Награда за общее начало имени: бренд и вид товара."""
    if len(first.words) >= 2 and first.words[:2] == second.words[:2]:
        return 0.70
    if first.words and second.words and first.words[0] == second.words[0]:
        return 0.40
    return 0.0


def pair_score(first: Item, second: Item) -> float:
    """Оценка пары «излишек — недостача»."""
    return similarity(first.key, second.key) + _name_bonus(first, second) + _price_bonus(first, second)


def _quantity_bonus(first: Item, second: Item) -> float:
    return 0.50 if abs(first.diff) == abs(second.diff) else 0.0


# Ниже этого порога пара не считается пересортом.
MATCH_MIN_SCORE = 0.75


class _Union:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, first: int, second: int) -> None:
        root_first, root_second = self.find(first), self.find(second)
        if root_first != root_second:
            self.parent[root_second] = root_first


def build_clusters(
    items: list[Item],
    threshold: float,
    doubtful_min: float,
    doubtful_max: float,
) -> tuple[list[Cluster], list[DoubtfulPair]]:
    """Подбирает пары пересорта: каждый излишек к своей недостаче."""
    union = _Union(len(items))
    plus = [index for index, item in enumerate(items) if item.diff > 0]
    minus = [index for index, item in enumerate(items) if item.diff < 0]

    doubtful: list[DoubtfulPair] = []
    ranked: list[tuple[float, int, int]] = []
    for p in plus:
        for m in minus:
            score = pair_score(items[p], items[m]) + _quantity_bonus(items[p], items[m])
            ranked.append((score, p, m))
            ratio = similarity(items[p].key, items[m].key)
            if doubtful_min <= ratio <= doubtful_max:
                doubtful.append(
                    DoubtfulPair(
                        first_row=items[p].row,
                        first_name=items[p].name,
                        second_row=items[m].row,
                        second_name=items[m].name,
                        ratio=round(ratio, 3),
                        linked=False,
                    )
                )

    ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    free_minus = {index: abs(items[index].diff) for index in minus}
    taken_plus: dict[int, int] = {}
    for score, p, m in ranked:
        if score < MATCH_MIN_SCORE:
            break
        if p in taken_plus or free_minus.get(m, 0) <= 0:
            continue
        taken_plus[p] = m
        free_minus[m] = max(0.0, free_minus[m] - abs(items[p].diff))
        union.union(p, m)

    for pair in doubtful:
        pair.linked = any(
            items[p].row == pair.first_row and items[m].row == pair.second_row
            for p, m in taken_plus.items()
        )

    buckets: dict[int, Cluster] = {}
    for index, item in enumerate(items):
        buckets.setdefault(union.find(index), Cluster()).items.append(item)
    clusters = [buckets[key] for key in sorted(buckets)]
    for cluster in clusters:
        cluster.items.sort(key=lambda item: item.row)
        cluster.decision = decide(cluster)
    return clusters, doubtful


def decide(cluster: Cluster) -> str:
    """Пересорт, излишек или недостача."""
    if cluster.has_minus and cluster.has_plus:
        return DECISION_RESORT
    if cluster.total_diff > 0:
        return DECISION_SURPLUS
    if cluster.total_diff < 0:
        return DECISION_SHORTAGE
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
