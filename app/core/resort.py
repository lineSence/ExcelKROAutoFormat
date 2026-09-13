"""Поиск пересортов внутри группы (правило docs/06-resort-rules.md).

Правило подбора пары повторяет ручную сверку:
1. Каждый излишек ищет себе недостачу в той же группе.
2. Пара возможна, если это один бренд (совпали два первых слова имени
   или имена похожи целиком) либо цены сопоставимы. Разные бренды с
   разной ценой в пару не ставятся.
3. Излишек по деньгам должен закрывать недостачу: цена излишка не ниже
   цены недостачи. Такие пары берутся первыми.
4. Количество в паре может не совпадать. Тогда к одной недостаче
   добавляются несколько излишков, пока количество не закроется.
5. В грозди может быть больше двух строк: строка без пары присоединяется
   к готовой грозди, если это тот же товар с уточнением в имени и той же ценой.
6. Излишки и недостачи без пары остаются излишками и недостачами.
7. Решения пользователя по спорным парам (decisions) главнее расчёта.

Режим «только чёткие пересорты» (`strict_brand_only=True`): в пару ставятся
только позиции одного бренда, цена в подборе не участвует вовсе.

Второй слой (`verify.py`) необязателен: модель судит только те пары,
которые предложил слой 1, и не умеет добавлять свои.

Зелёная заливка и метка `не-` — ручной тег по внешним данным. Программа его
не ставит и не воспроизводит.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

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

# Источник попадания пары в список спорных.
SOURCE_SIMILARITY = "схожесть"
SOURCE_MODEL = "модель"

# Границы отношения цены излишка к цене недостачи для пары разных брендов.
# Значения по умолчанию; рабочие берутся из настроек (.env).
PRICE_GATE_LOW = 0.95
PRICE_GATE_HIGH = 1.50

# Ниже этой оценки пара не считается пересортом.
MATCH_MIN_SCORE = 1.10

# Предел числа спорных пар в ответе: иначе страница разрастается.
DOUBTFUL_LIMIT = 200


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
    """Пара имён в полосе сомнения.

    Числа строк хранятся рядом: ответ человека сразу становится
    примером для обучения модели.
    """

    first_row: int
    first_name: str
    second_row: int
    second_name: str
    ratio: float
    linked: bool
    key: str = ""
    answered: bool = False
    first_diff: float = 0.0
    first_sum: float = 0.0
    second_diff: float = 0.0
    second_sum: float = 0.0
    # Шанс модели, если второй слой работал.
    model_prob: float | None = None
    source: str = SOURCE_SIMILARITY


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


@lru_cache(maxsize=200_000)
def _ratio(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def similarity(first: str, second: str) -> float:
    """Схожесть имён. Значения кешируются: одна пара считается один раз."""
    if first == second:
        return 1.0
    return _ratio(first, second) if first <= second else _ratio(second, first)


def maybe_similar(first: str, second: str, floor: float) -> bool:
    """Быстрый отсев явно разных имён без полного сравнения."""
    if not first or not second:
        return False
    matcher = SequenceMatcher(None, first, second)
    if matcher.real_quick_ratio() < floor:
        return False
    return matcher.quick_ratio() >= floor


def _price_bonus(first: Item, second: Item) -> float:
    """Награда за близкую цену."""
    high = max(first.price, second.price)
    if high <= 0:
        return 0.0
    gap = abs(first.price - second.price) / high
    if gap <= 0.001:
        return 0.30
    if gap <= 0.10:
        return 0.15
    if gap <= 0.25:
        return 0.05
    return 0.0


def _name_bonus(first: Item, second: Item) -> float:
    """Награда за общее начало имени: бренд и вид товара."""
    if len(first.words) >= 2 and first.words[:2] == second.words[:2]:
        return 1.00
    if first.words and second.words and first.words[0] == second.words[0]:
        return 0.50
    return 0.0


def same_brand(first: Item, second: Item, threshold: float) -> bool:
    """Один бренд: совпали два первых слова имени либо имена похожи целиком.

    Одно общее первое слово («зажигалка», «сувенир», «вода») брендом не
    считается: ручная сверка такие позиции в пару не ставит.
    """
    if len(first.words) >= 2 and first.words[:2] == second.words[:2]:
        return True
    if not maybe_similar(first.key, second.key, threshold):
        return False
    return similarity(first.key, second.key) >= threshold


def price_ratio(plus: Item, minus: Item) -> float:
    """Отношение цены излишка к цене недостачи."""
    if minus.price <= 0:
        return 0.0
    return plus.price / minus.price


def price_allowed(
    plus: Item,
    minus: Item,
    threshold: float,
    gate_low: float = PRICE_GATE_LOW,
    gate_high: float = PRICE_GATE_HIGH,
) -> bool:
    """Проверка цены для пары разных брендов."""
    if gate_low <= price_ratio(plus, minus) <= gate_high:
        return True
    return same_brand(plus, minus, threshold)


def covers(plus: Item, minus: Item) -> bool:
    """Излишек закрывает недостачу по деньгам: пересорт без потери суммы."""
    return plus.price + 1e-9 >= minus.price


def same_line(first: Item, second: Item, check_price: bool = True) -> bool:
    """Тот же товар с уточнением в имени (и той же ценой).

    Так в одну гроздь попадают «Мальборо» и «Мальборо компакт Дабл микс».
    В режиме только чётких пересортов цена не проверяется.
    """
    short, long = sorted((first.key, second.key), key=len)
    if not short or not long.startswith(short):
        return False
    if not check_price:
        return True
    high = max(first.price, second.price)
    if high <= 0:
        return False
    return abs(first.price - second.price) / high <= 0.001


def _quantity_bonus(first: Item, second: Item) -> float:
    return 0.80 if abs(first.diff) == abs(second.diff) else 0.0


def _distance_bonus(first: Item, second: Item) -> float:
    """Соседние строки связываются охотнее: список отсортирован по имени."""
    gap = abs(first.row - second.row)
    if gap <= 1:
        return 0.20
    if gap <= 3:
        return 0.10
    return 0.0


def pair_score(first: Item, second: Item) -> float:
    """Оценка пары «излишек — недостача»."""
    return (
        1.5 * (similarity(first.key, second.key) + _name_bonus(first, second))
        + _price_bonus(first, second)
        + 0.5 * _quantity_bonus(first, second)
        + (0.60 if covers(first, second) else 0.0)
        + _distance_bonus(first, second)
    )


def brand_score(first: Item, second: Item) -> float:
    """Оценка пары без учёта цены: режим только чётких пересортов."""
    return (
        1.5 * (similarity(first.key, second.key) + _name_bonus(first, second))
        + 0.5 * _quantity_bonus(first, second)
        + _distance_bonus(first, second)
    )


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


def pair_key(first_row: int, second_row: int) -> str:
    """Ключ решения пользователя по спорной паре."""
    return f"{min(first_row, second_row)}-{max(first_row, second_row)}"


def _doubtful(
    plus: Item,
    minus: Item,
    ratio: float,
    key: str,
    answered: bool,
    model_prob: float | None,
    source: str,
) -> DoubtfulPair:
    """Спорная пара со всеми числами для режима обучения."""
    return DoubtfulPair(
        first_row=plus.row,
        first_name=plus.name,
        second_row=minus.row,
        second_name=minus.name,
        ratio=round(ratio, 3),
        linked=False,
        key=key,
        answered=answered,
        first_diff=plus.diff,
        first_sum=round(plus.sum_diff, 2),
        second_diff=minus.diff,
        second_sum=round(minus.sum_diff, 2),
        model_prob=round(model_prob, 3) if model_prob is not None else None,
        source=source,
    )


def build_clusters(
    items: list[Item],
    threshold: float,
    doubtful_min: float,
    doubtful_max: float,
    decisions: dict[str, bool] | None = None,
    strict_brand_only: bool = False,
    price_gate_low: float = PRICE_GATE_LOW,
    price_gate_high: float = PRICE_GATE_HIGH,
    match_min_score: float = MATCH_MIN_SCORE,
    doubtful_limit: int = DOUBTFUL_LIMIT,
    verifier=None,
) -> tuple[list[Cluster], list[DoubtfulPair]]:
    """Подбирает пары пересорта: каждый излишек к своей недостаче.

    При `strict_brand_only=True` разрешены только пары одного бренда,
    а цена не влияет ни на допуск пары, ни на её оценку.

    `verifier` — второй слой. Он судит только готовые кандидаты: может
    снять пару, усилить её оценку или отправить в спорные. Решение
    человека (decisions) всегда главнее модели.

    Список спорных пар ограничен `doubtful_limit`: самые схожие пары идут
    в отчёт первыми.
    """
    union = _Union(len(items))
    plus = [index for index, item in enumerate(items) if item.diff > 0]
    minus = [index for index, item in enumerate(items) if item.diff < 0]

    choices = decisions or {}
    doubtful: list[DoubtfulPair] = []
    seen_doubtful: set[str] = set()
    ranked: list[tuple[float, int, int]] = []
    for p in plus:
        for m in minus:
            key = pair_key(items[p].row, items[m].row)
            answer = choices.get(key)
            if answer is False:
                continue
            if answer is not True:
                if strict_brand_only:
                    if not same_brand(items[p], items[m], threshold):
                        continue
                elif not price_allowed(
                    items[p], items[m], threshold, price_gate_low, price_gate_high
                ):
                    continue
            if strict_brand_only:
                score = brand_score(items[p], items[m])
            else:
                score = pair_score(items[p], items[m])

            # Второй слой: только для пар, по которым человек ещё не ответил.
            model_prob: float | None = None
            if verifier is not None and answer is not True:
                verdict = verifier.check(items[p], items[m])
                model_prob = verdict.prob
                if not verdict.accept:
                    continue
                score += verdict.bonus
                if verdict.gray and key not in seen_doubtful:
                    seen_doubtful.add(key)
                    doubtful.append(
                        _doubtful(
                            items[p],
                            items[m],
                            similarity(items[p].key, items[m].key),
                            key,
                            key in choices,
                            model_prob,
                            SOURCE_MODEL,
                        )
                    )

            if answer is True:
                score += 10.0
            ranked.append((score, p, m))
            # Полная схожесть считается только для прошедших отбор пар.
            ratio = similarity(items[p].key, items[m].key)
            if doubtful_min <= ratio <= doubtful_max and key not in seen_doubtful:
                seen_doubtful.add(key)
                doubtful.append(
                    _doubtful(
                        items[p],
                        items[m],
                        ratio,
                        key,
                        key in choices,
                        model_prob,
                        SOURCE_SIMILARITY,
                    )
                )

    ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    free_minus = {index: abs(items[index].diff) for index in minus}
    taken_plus: dict[int, int] = {}
    for score, p, m in ranked:
        if score < match_min_score:
            break
        if p in taken_plus or free_minus.get(m, 0) <= 0:
            continue
        taken_plus[p] = m
        free_minus[m] = max(0.0, free_minus[m] - abs(items[p].diff))
        union.union(p, m)

    # Добор в гроздь: строка без пары присоединяется к уже собранной грозди,
    # если это тот же товар с уточнением в имени.
    paired = set(taken_plus) | set(taken_plus.values())
    for index in range(len(items)):
        if index in paired:
            continue
        best: tuple[float, int] | None = None
        for other in paired:
            if not same_line(items[index], items[other], check_price=not strict_brand_only):
                continue
            ratio = similarity(items[index].key, items[other].key)
            if best is None or ratio > best[0]:
                best = (ratio, other)
        if best is not None:
            union.union(best[1], index)

    linked_pairs = {
        pair_key(items[p].row, items[m].row) for p, m in taken_plus.items()
    }
    for pair in doubtful:
        pair.linked = pair.key in linked_pairs

    # Сначала самые схожие пары, потом по номеру строки.
    doubtful.sort(key=lambda pair: (-pair.ratio, pair.first_row, pair.second_row))
    if doubtful_limit > 0:
        doubtful = doubtful[:doubtful_limit]

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
