"""Второй слой в виде полноценной LLM (OpenRouter или GigaChat).

Логистическая регрессия из `verify.py` считает признаки пары числами.
Здесь по той же паре спрашивают языковую модель: «это один товар?».
Модель не придумывает пары — она судит только тех кандидатов, которых
предложил слой 1 (`resort.py`).

Провайдеров два, выбор на странице «Модель и обучение»:

* **OpenRouter** — разрешён владельцем как исключение для второго слоя;
* **GigaChat (Сбер)** — тот же провайдер, что и для распознавания фото:
  доступен из России, ключ авторизации Basic меняется на токен доступа.
  Сеть и авторизацию берём из `vision.py`, чтобы не писать второй клиент.

Зачем это нужно: проверить, насколько хорошо модель решает сама по себе.
Поэтому влияние детерминированной логики регулируется отдельно
(`logic_weight`, поле 0…100% на странице загрузки). При 0% ворота по
бренду и цене кандидатов не отсеивают, а оценка пары складывается только
из ответа модели. Эмбеддинги имён (`embed.py`), если они включены, идут в
подсказку строкой «близость имён»: так LLM работает в связке с ними.

Что важно помнить:

- имена товаров, цены и количества уходят во внешний сервис;
- каждый запрос стоит денег, поэтому пары идут пачками (`judge_batch`),
  на файл есть предел пар (`judge_max_pairs`), а на процесс — предел
  запросов (`judge_max_requests`);
- ответы кэшируются по именам пары, а не по номерам строк: при
  пересборке файла строки сдвигаются, а имена остаются теми же;
- любая ошибка сети, ключа или разбора ответа не роняет сверку: пара
  получает нейтральный ответ (`Verdict.known = False`) и решается логикой;
- ключ задаётся только в интерфейсе (`/training`) и хранится в
  runtime.json, из окружения он не читается.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request

from .embed import mask_key
from .resort import Item
from .verify import Verdict

logger = logging.getLogger("excelkro.judge")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

# Провайдеры второго слоя и их значения по умолчанию.
OPENROUTER = "openrouter"
GIGACHAT = "gigachat"
PROVIDERS = (OPENROUTER, GIGACHAT)
PROVIDER_LABELS = {
    OPENROUTER: "OpenRouter",
    GIGACHAT: "GigaChat (Сбер)",
}
GIGACHAT_MODEL = "GigaChat-2"

SYSTEM_PROMPT = (
    "Ты помогаешь сверять инвентаризацию табачного магазина. "
    "Тебе дают пары строк: излишек (нашли лишнее) и недостача (не хватает). "
    "По каждой паре реши, один ли это товар, просто названный по-разному или "
    "перепутанный при продаже (пересорт). Учитывай бренд, вид товара, вкус, "
    "крепость, объём, цену и количество. Разные бренды и разные вкусы — не "
    "один товар, даже если цена совпала. Отвечай только JSON, без пояснений."
)

ANSWER_RULE = (
    'Ответ строго в виде {"answers": [{"id": 1, "p": 0.9}]}, где id — номер '
    "пары из списка, а p — вероятность от 0 до 1, что это один и тот же товар."
)

JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class JudgeError(RuntimeError):
    """Ответ не получен. Пара остаётся на усмотрение логики."""


def provider_name(settings) -> str:
    """Выбранный провайдер. Неизвестное значение — OpenRouter."""
    name = str(getattr(settings, "judge_provider", "") or "").strip().lower()
    return name if name in PROVIDERS else OPENROUTER


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(str(name or "").strip().lower(), PROVIDER_LABELS[OPENROUTER])


def default_model(provider: str) -> str:
    return GIGACHAT_MODEL if provider == GIGACHAT else DEFAULT_MODEL


def clamp_weight(value) -> float:
    """Доля влияния логики: число от 0 до 1."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    return min(1.0, max(0.0, number))


def _post(url: str, payload: dict, api_key: str, timeout: float, retries: int) -> dict:
    """Запрос к чату модели. Ошибки — понятным текстом на русском."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("X-Title", "ExcelKROAutoFormat")

    last = ""
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return json.loads(answer.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:300]
            except OSError:
                detail = ""
            last = f"сервис ответил {error.code}: {detail}".strip()
            # Ключ, модель и тариф повторной попыткой не исправить.
            if error.code < 500 and error.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = f"нет связи с сервисом LLM: {error}"
        except ValueError as error:
            last = f"непонятный ответ сервиса: {error}"
            break
        if attempt + 1 < attempts:
            time.sleep(1.0)
    raise JudgeError(last or "ответ модели не получен")


def _post_gigachat(payload: dict, api_key: str, timeout: float, retries: int) -> dict:
    """Тот же запрос, но через GigaChat.

    Авторизация у Сбера двухшаговая и уже написана в `vision.py`,
    поэтому берём тот же клиент. Ответ приводим к тому же виду,
    что у OpenRouter, чтобы разбор был общий.
    """
    from . import vision as vision_core

    client = vision_core.GigaChatVision(
        auth_key=str(api_key or ""),
        model=str(payload.get("model") or GIGACHAT_MODEL),
        timeout=int(timeout or 60),
        retries=int(retries or 0),
    )
    text, error = client._chat(payload)
    if error:
        raise JudgeError(error)
    return {"choices": [{"message": {"content": text}}]}


def _answers_from(answer: dict, count: int) -> dict[int, float]:
    """Разбор ответа чата: JSON со списком вероятностей по номерам пар."""
    choices = answer.get("choices") if isinstance(answer, dict) else None
    if not isinstance(choices, list) or not choices:
        raise JudgeError("в ответе сервиса нет ответа модели")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise JudgeError("модель ответила пустым текстом")

    found = JSON_BLOCK.search(text)
    if found is None:
        raise JudgeError(f"ответ модели не похож на JSON: {text[:120]}")
    try:
        data = json.loads(found.group(0))
    except ValueError as error:
        raise JudgeError(f"ответ модели не разобран: {error}") from error

    rows = data.get("answers") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise JudgeError("в ответе модели нет списка answers")

    result: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("id"))
            prob = float(row.get("p"))
        except (TypeError, ValueError):
            continue
        result[number] = min(1.0, max(0.0, prob))
    if not result:
        raise JudgeError("в ответе модели нет пар вида id и p")
    if len(result) < count:
        logger.warning(
            "Модель ответила не по всем парам: спрошено %s, получено %s",
            count,
            len(result),
        )
    return result


class LlmJudge:
    """Второй слой на языковой модели: судит пары пачками и кэширует ответы.

    `sender` подменяется в тестах: это единственное место, где идёт сеть.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        url: str = OPENROUTER_URL,
        timeout: float = 60.0,
        retries: int = 1,
        max_requests: int = 60,
        batch: int = 20,
        max_pairs: int = 200,
        embedder=None,
        reject: float = 0.35,
        gray_low: float = 0.40,
        gray_high: float = 0.60,
        weight: float = 0.30,
        logic_weight: float = 1.0,
        sender=None,
        provider: str = OPENROUTER,
    ) -> None:
        self.provider = provider if provider in PROVIDERS else OPENROUTER
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip() or default_model(self.provider)
        self.url = str(url or "").strip() or OPENROUTER_URL
        self.timeout = float(timeout or 60.0)
        self.retries = int(retries or 0)
        self.max_requests = int(max_requests or 0)
        self.batch = max(1, int(batch or 1))
        self.max_pairs = int(max_pairs or 0)
        self.embedder = embedder
        self.reject = float(reject)
        self.gray_low = float(gray_low)
        self.gray_high = float(gray_high)
        self.weight = float(weight)
        self.logic_weight = clamp_weight(logic_weight)
        self.sender = sender
        # Ответы модели по именам пары, счётчики и последняя беда.
        self.answers: dict[str, float] = {}
        self.sent = 0
        self.judged = 0
        self.last_error = ""

    # ——— подсказка ———

    @staticmethod
    def _key(plus: Item, minus: Item) -> str:
        """Ключ ответа: нормализованные имена, а не номера строк."""
        return f"{plus.key}=>{minus.key}"

    def _cos(self, plus: Item, minus: Item) -> float | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.cos(plus.name, minus.name)
        except Exception:  # noqa: BLE001 — эмбеддинги не должны ломать сверку
            return None

    def _line(self, number: int, plus: Item, minus: Item) -> str:
        cos = self._cos(plus, minus)
        tail = f", близость имён по эмбеддингам {cos:.2f}" if cos is not None else ""
        return (
            f"{number}. Излишек: «{plus.name}», цена {plus.price:.2f}, "
            f"{abs(plus.diff):g} шт. Недостача: «{minus.name}», цена "
            f"{minus.price:.2f}, {abs(minus.diff):g} шт.{tail}"
        )

    def _send(self, payload: dict) -> dict:
        if self.sender is not None:
            return self.sender(payload)
        if self.provider == GIGACHAT:
            return _post_gigachat(payload, self.api_key, self.timeout, self.retries)
        return _post(self.url, payload, self.api_key, self.timeout, self.retries)

    def _ask(self, chunk: list[tuple[str, Item, Item]]) -> dict[str, float]:
        """Один запрос на пачку пар."""
        if not self.api_key:
            raise JudgeError(f"не задан ключ {provider_label(self.provider)}")
        lines = [
            self._line(number, plus, minus)
            for number, (_, plus, minus) in enumerate(chunk, start=1)
        ]
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines) + "\n\n" + ANSWER_RULE},
            ],
        }
        if self.provider == GIGACHAT:
            # У Сбера нулевая температура не принимается: берём минимальную.
            payload["temperature"] = 0.1
            payload["max_tokens"] = 800
        self.sent += 1
        numbers = _answers_from(self._send(payload), len(chunk))
        found: dict[str, float] = {}
        for number, prob in numbers.items():
            if 1 <= number <= len(chunk):
                found[chunk[number - 1][0]] = prob
        return found

    # ——— работа ———

    def prefetch(self, pairs) -> None:
        """Спрашивает модель по всем новым парам пачками.

        Слой 1 передаёт кандидатов в порядке своей оценки, поэтому при
        исчерпании предела судятся самые похожие пары, а остальные
        остаются на логике.
        """
        wanted: list[tuple[str, Item, Item]] = []
        seen: set[str] = set()
        for plus, minus in pairs:
            key = self._key(plus, minus)
            if key in self.answers or key in seen:
                continue
            seen.add(key)
            wanted.append((key, plus, minus))
        if not wanted:
            return

        if self.max_pairs > 0:
            room = self.max_pairs - self.judged
            if room <= 0:
                self.last_error = f"предел пар на файл исчерпан ({self.max_pairs})"
                return
            wanted = wanted[:room]

        for start in range(0, len(wanted), self.batch):
            chunk = wanted[start : start + self.batch]
            if self.max_requests > 0 and self.sent >= self.max_requests:
                self.last_error = f"предел запросов исчерпан ({self.max_requests})"
                logger.warning("LLM-судья: %s", self.last_error)
                return
            try:
                self.answers.update(self._ask(chunk))
            except JudgeError as error:
                self.last_error = str(error)
                logger.warning("LLM-судья не ответил: %s", error)
                return
            except Exception as error:  # noqa: BLE001 — сверка не должна падать
                self.last_error = f"{type(error).__name__}: {error}"
                logger.exception("LLM-судья не ответил")
                return
            self.judged += len(chunk)

    def check(self, plus: Item, minus: Item) -> Verdict:
        """Ответ по одной паре. Неизвестную пару спрашиваем на месте."""
        key = self._key(plus, minus)
        if key not in self.answers:
            self.prefetch([(plus, minus)])
        prob = self.answers.get(key)
        if prob is None:
            reason = "LLM без ответа"
            if self.last_error:
                reason = f"LLM без ответа: {self.last_error}"
            return Verdict(0.5, True, False, 0.0, reason, known=False)
        if prob < self.reject:
            return Verdict(prob, False, False, 0.0, "LLM: не один товар")
        gray = self.gray_low <= prob <= self.gray_high
        reason = "LLM: сомнительно" if gray else "LLM: один товар"
        return Verdict(prob, True, gray, self.weight * (prob - 0.5), reason)


def _judge_from(settings, logic_weight: float = 1.0, embedder=None) -> LlmJudge:
    """Судья по настройкам интерфейса."""
    provider = provider_name(settings)
    return LlmJudge(
        api_key=str(getattr(settings, "judge_api_key", "") or ""),
        model=str(getattr(settings, "judge_model", "") or default_model(provider)),
        url=str(getattr(settings, "judge_api_url", "") or OPENROUTER_URL),
        timeout=float(getattr(settings, "judge_timeout", 60.0) or 60.0),
        retries=int(getattr(settings, "judge_retries", 1) or 0),
        max_requests=int(getattr(settings, "judge_max_requests", 60) or 0),
        batch=int(getattr(settings, "judge_batch", 20) or 1),
        max_pairs=int(getattr(settings, "judge_max_pairs", 200) or 0),
        embedder=embedder,
        reject=float(getattr(settings, "verify_reject", 0.35) or 0.0),
        gray_low=float(getattr(settings, "verify_gray_low", 0.40) or 0.0),
        gray_high=float(getattr(settings, "verify_gray_high", 0.60) or 0.0),
        weight=float(getattr(settings, "verify_weight", 0.30) or 0.0),
        logic_weight=logic_weight,
        provider=provider,
    )


_JUDGE: LlmJudge | None = None
_MARKER: str | None = None


def _marker(settings) -> str:
    """Отпечаток настроек: по нему понятно, нужен ли новый судья."""
    key = str(getattr(settings, "judge_api_key", "") or "")
    return "|".join(
        (
            provider_name(settings),
            str(getattr(settings, "judge_model", "")),
            str(getattr(settings, "judge_api_url", "")),
            mask_key(key),
            str(bool(key)),
            str(getattr(settings, "judge_batch", "")),
            str(getattr(settings, "judge_max_pairs", "")),
            str(getattr(settings, "judge_max_requests", "")),
        )
    )


def load_judge(settings, logic_weight: float = 1.0, embedder=None) -> LlmJudge | None:
    """Судья на процесс. None — второй слой не работает, решает логика.

    Ответы модели переживают пересборку файла: они кэшированы по именам.
    Предел пар считается заново на каждый файл, предел запросов — на процесс.
    """
    global _JUDGE, _MARKER

    if not str(getattr(settings, "judge_api_key", "") or "").strip():
        logger.warning("Не задан ключ для LLM: работаем без второго слоя.")
        return None

    marker = _marker(settings)
    if _JUDGE is None or _MARKER != marker:
        _JUDGE = _judge_from(settings, logic_weight, embedder)
        _MARKER = marker
        return _JUDGE

    _JUDGE.logic_weight = clamp_weight(logic_weight)
    _JUDGE.embedder = embedder
    _JUDGE.judged = 0
    _JUDGE.last_error = ""
    return _JUDGE


def forget_judge() -> None:
    """Сбрасывает судью: нужно после смены настроек в интерфейсе."""
    global _JUDGE, _MARKER
    _JUDGE = None
    _MARKER = None


def check_key(settings) -> tuple[bool, str]:
    """Проверка ключа и модели одной короткой парой. Кэш не трогается."""
    provider = provider_name(settings)
    if not str(getattr(settings, "judge_api_key", "") or "").strip():
        return False, (
            f"Сначала введите ключ {provider_label(provider)} и сохраните настройки."
        )

    judge = _judge_from(settings)
    judge.max_requests = 0
    judge.max_pairs = 0
    judge.batch = 1
    plus = Item(
        row=1,
        name="Мальборо Голд",
        diff=1.0,
        sum_diff=200.0,
        key="marlboro gold",
        words=("marlboro", "gold"),
    )
    minus = Item(
        row=2,
        name="Marlboro Gold",
        diff=-1.0,
        sum_diff=-200.0,
        key="marlboro gold",
        words=("marlboro", "gold"),
    )
    judge.prefetch([(plus, minus)])
    prob = judge.answers.get(judge._key(plus, minus))
    if prob is None:
        return False, f"Проверка не прошла: {judge.last_error or 'модель не ответила'}"
    return True, (
        f"Ключ работает: {provider_label(provider)}, модель {judge.model} ответила "
        f"по пробной паре, шанс одного товара {prob:.2f}."
    )


def status(settings) -> dict:
    """Состояние LLM-судьи для интерфейса."""
    key = str(getattr(settings, "judge_api_key", "") or "").strip()
    provider = provider_name(settings)
    model = str(getattr(settings, "judge_model", "") or default_model(provider))
    logic = clamp_weight(getattr(settings, "logic_weight", 1.0))
    label = provider_label(provider)
    if key and provider == GIGACHAT:
        reason = (
            f"Ключ задан, модель {model}. Пары уходят в GigaChat пачками "
            "и тратят токены ключа."
        )
    elif key:
        reason = (
            f"Ключ задан, модель {model}. Пары уходят в OpenRouter пачками "
            "и тратят баланс ключа."
        )
    else:
        reason = (
            f"Ключ {label} не задан: режим «Логика + LLM» работать не будет, "
            "сверка пойдёт на одной логике."
        )
    return {
        "ready": bool(key),
        "has_key": bool(key),
        "key_tail": mask_key(key),
        "provider": provider,
        "provider_label": label,
        "providers": [
            {"value": name, "label": provider_label(name)} for name in PROVIDERS
        ],
        "model": model,
        "api_url": str(getattr(settings, "judge_api_url", "") or OPENROUTER_URL),
        "timeout": float(getattr(settings, "judge_timeout", 60.0) or 60.0),
        "retries": int(getattr(settings, "judge_retries", 1) or 0),
        "max_requests": int(getattr(settings, "judge_max_requests", 60) or 0),
        "batch": int(getattr(settings, "judge_batch", 20) or 1),
        "max_pairs": int(getattr(settings, "judge_max_pairs", 200) or 0),
        "logic_weight": logic,
        "logic_percent": int(round(logic * 100)),
        "reason": reason,
    }
