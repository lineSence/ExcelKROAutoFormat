"""Связка письма ревизора со сверкой по названию магазина.

Зачем модуль нужен. Ящик один на всю службу, а писем в нём десятки: за
смену ревизоры присылают снимки по разным магазинам. Раньше при обработке
одной сверки в нейросеть уходили снимки **всех** известных писем, а на
странице готовой сверки показывались кнопки архивов всех писем подряд. Из
этого выходило два вреда: запросы к GigaChat тратились на чужой магазин, а
человеку предлагались фото товара, которого в этой сверке нет.

Что делает модуль. До любой отправки снимков в модель он сопоставляет
название склада сверки (`summary["warehouse"]`, например
«ВыборгРебусПДВ») с темой письма: ревизоры пишут в теме название магазина
(«магазин Ребус», «Ребус ПДВ», «маг. 47»). Совпало — письмо считается
относящимся к этой сверке, не совпало — письмо в модель не уходит вовсе.

Почему сравнение такое простое. Никакой модели здесь быть не должно:
связка письма со сверкой — детерминированный шаг, её должно быть видно
человеку и легко объяснить. Поэтому берутся только слова:

* название склада режется на части и по словам, и по заглавным буквам:
  «ВыборгРебусПДВ» → «выборг», «ребус», «пдв»;
* из темы письма берутся слова и подсказка `hints["store"]`, служебные
  слова («магазин», «фото», «излишек») отбрасываются;
* «ё» превращается в «е», регистр и знаки не важны;
* часть склада, найденная в теме целиком, — совпадение; иначе близость
  слов считает `difflib` (опечатки вида «Богатырский»/«Богатырской»).

Номер магазина («магазин 47») тоже совпадение, если это число есть в
названии склада.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger("excelkro.mail_match")

# Насколько похожими должны быть слова, чтобы считать их одним магазином.
MIN_RATIO = 0.82

# Короткие слова не сравниваем: «дом», «ул» дают ложные совпадения.
MIN_PART = 4

# Слова, которые в теме письма встречаются всегда и о магазине не говорят.
STOP_WORDS = frozenset(
    {
        "магазин",
        "магазина",
        "магазину",
        "маг",
        "точка",
        "точке",
        "фото",
        "фотографии",
        "снимки",
        "товар",
        "товара",
        "излишек",
        "излишки",
        "недостача",
        "пересорт",
        "ревизор",
        "ревизия",
        "инвентаризация",
        "сверка",
        "сверке",
        "письмо",
        "отчет",
        "опись",
        "список",
        "добрый",
        "день",
        "здравствуйте",
        "пересылка",
        "без",
        "темы",
        "для",
        "про",
        "по",
        "от",
        "на",
        "и",
        "в",
    }
)

WORDS = re.compile(r"[а-яa-z0-9]+")
CAMEL = re.compile(r"[А-ЯЁA-Z]+[а-яёa-z]*|[а-яёa-z]+|\d+")

# Что показать человеку, когда письма этого магазина не нашлись.
NO_MATCH = (
    "Письма по этому магазину в ящике нет: снимки в нейросеть не отправлялись. "
    "Проверьте тему письма — ревизор пишет в ней название магазина, — "
    "или отправьте нужное письмо на разбор вручную в разделе «Почта ревизоров»."
)


def clean(text: object) -> str:
    """Текст в едином виде: нижний регистр и «е» вместо «ё»."""
    return str(text or "").strip().lower().replace("ё", "е")


def parts(text: object) -> list[str]:
    """Слова названия: и по пробелам, и по заглавным буквам.

    «ВыборгРебусПДВ» и «Выборг Ребус ПДВ» дают один и тот же список, так
    что название склада из 1С сравнивается с темой письма без ручных
    справочников.
    """
    found: list[str] = []
    for word in CAMEL.findall(str(text or "")):
        piece = clean(word)
        if piece and piece not in found:
            found.append(piece)
    return found


def store_parts(subject: object, store: object = "") -> list[str]:
    """Слова темы письма, по которым можно узнать магазин."""
    found: list[str] = []
    for source in (store, subject):
        for word in WORDS.findall(clean(source)):
            if word in STOP_WORDS or word in found:
                continue
            if len(word) < MIN_PART and not word.isdigit():
                continue
            found.append(word)
    return found


def _ratio(one: str, two: str) -> float:
    return SequenceMatcher(None, one, two).ratio()


def score(warehouse: object, subject: object, store: object = "") -> tuple[float, str]:
    """Насколько тема письма похожа на название склада сверки.

    Возвращает пару «оценка, объяснение». Оценка 1.0 — слово названия
    найдено в теме как есть, меньше — близкое по написанию слово.
    """
    goals = parts(warehouse)
    said = store_parts(subject, store)
    if not goals or not said:
        return 0.0, ""
    best = 0.0
    reason = ""
    for goal in goals:
        if goal.isdigit():
            if goal in said:
                return 1.0, f"номер магазина {goal}"
            continue
        if len(goal) < MIN_PART:
            continue
        for word in said:
            if word == goal or (len(word) >= MIN_PART and (word in goal or goal in word)):
                return 1.0, f"слово «{goal}»"
            if word.isdigit():
                continue
            close = _ratio(word, goal)
            if close > best:
                best = close
                reason = f"«{word}» похоже на «{goal}»"
    if best >= MIN_RATIO:
        return best, reason
    return 0.0, ""


def fits(warehouse: object, letter, min_ratio: float = MIN_RATIO) -> tuple[bool, str]:
    """Относится ли письмо к этой сверке. Отдаёт и причину решения."""
    hints = getattr(letter, "hints", None) or {}
    value, reason = score(warehouse, getattr(letter, "subject", ""), hints.get("store", ""))
    if value >= min_ratio:
        return True, reason
    return False, reason


def split(warehouse: object, letters, min_ratio: float = MIN_RATIO) -> dict:
    """Делит письма на «наши» и «чужие» по названию магазина.

    Отчёт: `mine` — письма этой сверки, `others` — остальные, `reasons` —
    по номеру письма, чем оно совпало, `note` — что сказать человеку,
    если своих писем не нашлось.
    """
    mine: list = []
    others: list = []
    reasons: dict[str, str] = {}
    store = str(warehouse or "").strip()
    if not store:
        # Склад в сверке не определился: фильтровать нечем, но и слать
        # снимки всех писем в модель нельзя — это и была прежняя беда.
        logger.warning("Склад сверки не определён: письма со сверкой не связываются")
        return {
            "mine": [],
            "others": list(letters),
            "reasons": {},
            "warehouse": "",
            "note": (
                "В сверке не определилось название склада, поэтому письмо с ней "
                "не связано и снимки в нейросеть не отправлялись. Отправьте нужное "
                "письмо на разбор вручную в разделе «Почта ревизоров»."
            ),
        }
    for letter in letters:
        good, reason = fits(store, letter, min_ratio)
        if good:
            mine.append(letter)
            reasons[str(getattr(letter, "uid", ""))] = reason
        else:
            others.append(letter)
    note = "" if mine else NO_MATCH
    logger.info("Склад %s: своих писем %d, чужих %d", store, len(mine), len(others))
    return {
        "mine": mine,
        "others": others,
        "reasons": reasons,
        "warehouse": store,
        "note": note,
    }
