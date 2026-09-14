"""Второй слой на LLM и влияние детерминированной логики.

Сеть здесь не нужна: у `LlmJudge` подменяется `sender`, то есть
единственное место, где идут запросы.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.config import Settings
from app.core import judge as judge_core
from app.core import runtime
from app.core.judge import JudgeError, LlmJudge, clamp_weight
from app.core.resort import (
    DECISION_RESORT,
    MATCH_MIN_SCORE,
    build_clusters,
    make_item,
    mixed_score,
)
from app.core.verify import Verdict, load_verifier

TYPE_WORDS = ("сигареты", "зажигалка")


def answer(pairs: dict[int, float]) -> dict:
    """Ответ сервиса в том виде, в каком его отдаёт OpenRouter."""
    body = {"answers": [{"id": number, "p": prob} for number, prob in pairs.items()]}
    return {"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]}


def items_pair():
    """Пара разных брендов с разной ценой: ворота логики её не пускают."""
    plus = make_item(2, "Мальборо Голд", 1, 200, TYPE_WORDS)
    minus = make_item(3, "Кент Синий", -1, -100, TYPE_WORDS)
    return plus, minus


# --- разбор ответа ---------------------------------------------------------


def test_answers_parsed_with_extra_text():
    """Модель часто добавляет пояснения вокруг JSON — он всё равно разбирается."""
    payload = {
        "choices": [
            {"message": {"content": 'Вот ответ: {"answers": [{"id": 1, "p": 0.8}]} готово'}}
        ]
    }
    assert judge_core._answers_from(payload, 1) == {1: 0.8}


def test_answers_probability_clamped():
    assert judge_core._answers_from(answer({1: 5.0, 2: -1.0}), 2) == {1: 1.0, 2: 0.0}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "не знаю"}}]},
        {"choices": [{"message": {"content": '{"answers": []}'}}]},
    ],
)
def test_broken_answer_raises(payload):
    with pytest.raises(JudgeError):
        judge_core._answers_from(payload, 1)


# --- работа судьи ----------------------------------------------------------


def test_answer_cached_by_names():
    """Ответ кэшируется по именам: повторный вопрос не тратит запрос."""
    calls = []

    def sender(payload):
        calls.append(payload)
        return answer({1: 0.9})

    plus, minus = items_pair()
    judge = LlmJudge(api_key="key", sender=sender)

    first = judge.check(plus, minus)
    # Тот же товар, но строки сдвинулись — ключ кэша не меняется.
    moved_plus = make_item(20, "Мальборо Голд", 1, 200, TYPE_WORDS)
    moved_minus = make_item(30, "Кент Синий", -1, -100, TYPE_WORDS)
    second = judge.check(moved_plus, moved_minus)

    assert len(calls) == 1
    assert first.prob == pytest.approx(0.9)
    assert second.prob == pytest.approx(0.9)
    assert first.accept and second.accept


def test_pairs_go_in_batches():
    sent = []

    def sender(payload):
        lines = payload["messages"][1]["content"]
        sent.append(lines)
        return answer({1: 0.7, 2: 0.7})

    judge = LlmJudge(api_key="key", batch=2, sender=sender)
    pairs = [
        (
            make_item(index, f"Товар {index}", 1, 100, TYPE_WORDS),
            make_item(index + 100, f"Товар {index} новый", -1, -100, TYPE_WORDS),
        )
        for index in range(1, 5)
    ]
    judge.prefetch(pairs)

    assert len(sent) == 2
    assert judge.judged == 4


def test_request_limit_stops_asking():
    calls = []

    def sender(payload):
        calls.append(payload)
        return answer({1: 0.9})

    judge = LlmJudge(api_key="key", batch=1, max_requests=1, sender=sender)
    pairs = [
        (
            make_item(index, f"Товар {index}", 1, 100, TYPE_WORDS),
            make_item(index + 100, f"Товар {index} другой", -1, -100, TYPE_WORDS),
        )
        for index in range(1, 4)
    ]
    judge.prefetch(pairs)

    assert len(calls) == 1
    assert "предел запросов" in judge.last_error


def test_pair_limit_per_file():
    judge = LlmJudge(
        api_key="key",
        batch=10,
        max_pairs=2,
        sender=lambda payload: answer({1: 0.9, 2: 0.9}),
    )
    pairs = [
        (
            make_item(index, f"Товар {index}", 1, 100, TYPE_WORDS),
            make_item(index + 100, f"Товар {index} другой", -1, -100, TYPE_WORDS),
        )
        for index in range(1, 5)
    ]
    judge.prefetch(pairs)

    assert judge.judged == 2
    assert len(judge.answers) == 2


def test_silent_model_gives_unknown_verdict():
    """Сбой сети не роняет сверку: пара уходит на усмотрение логики."""

    def sender(payload):
        raise JudgeError("нет связи с сервисом LLM")

    plus, minus = items_pair()
    verdict = LlmJudge(api_key="key", sender=sender).check(plus, minus)

    assert verdict.known is False
    assert verdict.accept is True
    assert verdict.bonus == 0.0


def test_no_key_gives_unknown_verdict():
    plus, minus = items_pair()
    verdict = LlmJudge(api_key="").check(plus, minus)

    assert verdict.known is False
    assert "ключ" in verdict.reason


def test_embedding_closeness_goes_into_prompt():
    """Связка с эмбеддингами: близость имён попадает в подсказку модели."""
    seen = []

    class Embedder:
        def cos(self, first, second):
            return 0.87

    def sender(payload):
        seen.append(payload["messages"][1]["content"])
        return answer({1: 0.9})

    plus, minus = items_pair()
    judge = LlmJudge(api_key="key", embedder=Embedder(), sender=sender)
    judge.check(plus, minus)

    assert "близость имён по эмбеддингам 0.87" in seen[0]


def test_broken_embedder_does_not_break_prompt():
    class Embedder:
        def cos(self, first, second):
            raise RuntimeError("файл модели пропал")

    plus, minus = items_pair()
    judge = LlmJudge(
        api_key="key", embedder=Embedder(), sender=lambda payload: answer({1: 0.9})
    )

    assert judge.check(plus, minus).prob == pytest.approx(0.9)


# --- влияние логики --------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(0, 0.0), (0.5, 0.5), (1, 1.0), (2, 1.0), (-1, 0.0), ("нет", 1.0), (None, 1.0)],
)
def test_clamp_weight(value, expected):
    assert clamp_weight(value) == pytest.approx(expected)


def test_mixed_score_edges():
    # Полное влияние логики — оценка модели не учитывается.
    assert mixed_score(1.5, 0.1, 1.0) == pytest.approx(1.5)
    # Без влияния логики шанс 0.5 равен порогу.
    assert mixed_score(99.0, 0.5, 0.0) == pytest.approx(MATCH_MIN_SCORE)
    assert mixed_score(99.0, 0.9, 0.0) > MATCH_MIN_SCORE
    assert mixed_score(99.0, 0.2, 0.0) < MATCH_MIN_SCORE


class FakeVerifier:
    """Второй слой с заданным ответом: нужен для проверки веса логики."""

    def __init__(self, prob: float, logic_weight: float = 1.0, known: bool = True) -> None:
        self.prob = prob
        self.logic_weight = logic_weight
        self.known = known
        self.asked: list[tuple[int, int]] = []

    def prefetch(self, pairs) -> None:
        self.asked = [(plus.row, minus.row) for plus, minus in pairs]

    def check(self, plus, minus):
        return Verdict(self.prob, True, False, 0.0, "тест", known=self.known)


def clusters_for(weight: float, prob: float = 0.9, known: bool = True):
    plus, minus = items_pair()
    verifier = FakeVerifier(prob, logic_weight=weight, known=known)
    clusters, doubtful = build_clusters(
        [plus, minus],
        threshold=0.82,
        doubtful_min=0.60,
        doubtful_max=0.80,
        verifier=verifier,
        logic_weight=weight,
    )
    return clusters, doubtful, verifier


def test_full_logic_keeps_gates():
    """100% логики — пара разных брендов с разной ценой не собирается."""
    clusters, _, verifier = clusters_for(1.0)

    assert len(clusters) == 2
    assert all(cluster.decision != DECISION_RESORT for cluster in clusters)
    # Ворота отсеяли пару до второго слоя: спрашивать было нечего.
    assert verifier.asked == []


def test_zero_logic_lets_model_decide():
    """0% логики — ворота мягкие, пару собирает ответ модели."""
    clusters, _, verifier = clusters_for(0.0, prob=0.9)

    assert len(clusters) == 1
    assert clusters[0].decision == DECISION_RESORT
    assert verifier.asked == [(2, 3)]


def test_zero_logic_model_refuses():
    """0% логики и низкий шанс — пара не собирается."""
    clusters, _, _ = clusters_for(0.0, prob=0.1)

    assert len(clusters) == 2


def test_silent_model_falls_back_to_gates():
    """Модель промолчала: негейтовая пара отбрасывается даже при 0%."""
    clusters, _, _ = clusters_for(0.0, prob=0.9, known=False)

    assert len(clusters) == 2


def test_weight_ignored_without_second_layer():
    """Без второго слоя вес логики ничего не меняет."""
    plus, minus = items_pair()
    clusters, _ = build_clusters(
        [plus, minus],
        threshold=0.82,
        doubtful_min=0.60,
        doubtful_max=0.80,
        verifier=None,
        logic_weight=0.0,
    )

    assert len(clusters) == 2


# --- настройки в интерфейсе ------------------------------------------------


@pytest.fixture
def judge_settings(tmp_path):
    """Настройки с runtime.json в отдельной папке."""
    judge_core.forget_judge()
    runtime._CHOSEN.clear()
    base = replace(
        Settings.load(),
        train_store_path=str(tmp_path / "data" / "samples.jsonl"),
        judge_api_key="",
        embed_enabled=False,
    )
    yield base
    judge_core.forget_judge()
    runtime._CHOSEN.clear()


def test_save_judge_writes_runtime(judge_settings):
    runtime.save_judge(
        judge_settings,
        {
            "judge_api_key": "sk-test-1234567890",
            "judge_model": "openai/gpt-4o-mini",
            "judge_batch": "5",
            "judge_max_pairs": "50",
            "logic_weight": 0.0,
        },
    )
    stored = runtime.load(runtime.runtime_path(judge_settings))

    assert stored["judge_api_key"] == "sk-test-1234567890"
    assert stored["judge_batch"] == 5
    assert stored["logic_weight"] == 0.0

    applied = runtime.apply(judge_settings)
    assert applied.judge_batch == 5
    assert applied.logic_weight == 0.0

    status = runtime.judge_status(applied)
    assert status["has_key"] is True
    assert status["logic_percent"] == 0
    # Ключ на страницу целиком не уходит.
    assert "sk-test-1234567890" not in status["key_tail"]


def test_save_judge_clamps_weight(judge_settings):
    runtime.save_judge(judge_settings, {"logic_weight": 3.0})
    assert runtime.apply(judge_settings).logic_weight == 1.0


def test_empty_key_keeps_previous(judge_settings):
    runtime.save_judge(judge_settings, {"judge_api_key": "sk-first"})
    runtime.save_judge(judge_settings, {"judge_api_key": "", "judge_batch": "7"})
    stored = runtime.load(runtime.runtime_path(judge_settings))

    assert stored["judge_api_key"] == "sk-first"
    assert stored["judge_batch"] == 7


def test_forget_judge_key(judge_settings):
    runtime.save_judge(judge_settings, {"judge_api_key": "sk-first"})
    runtime.forget_judge_key(judge_settings)

    assert runtime.apply(judge_settings).judge_api_key == ""
    assert runtime.judge_status(runtime.apply(judge_settings))["has_key"] is False


def test_status_without_key(judge_settings):
    status = runtime.judge_status(judge_settings)

    assert status["ready"] is False
    assert "Логика + LLM" in status["reason"]


# --- выбор второго слоя ----------------------------------------------------


def test_load_verifier_llm_mode(judge_settings):
    settings = replace(judge_settings, verify_mode="llm", judge_api_key="sk-test")
    verifier = load_verifier(settings, 0.25)

    assert isinstance(verifier, LlmJudge)
    assert verifier.logic_weight == pytest.approx(0.25)


def test_load_verifier_llm_without_key(judge_settings):
    settings = replace(judge_settings, verify_mode="llm")

    assert load_verifier(settings) is None


def test_load_verifier_off_mode(judge_settings):
    settings = replace(judge_settings, verify_mode="off", judge_api_key="sk-test")

    assert load_verifier(settings) is None


def test_check_key_uses_probe_pair(monkeypatch, judge_settings):
    """Проверка ключа идёт одной парой и не трогает общий кэш судьи."""
    settings = replace(judge_settings, judge_api_key="sk-test")
    monkeypatch.setattr(
        judge_core, "_post", lambda *args, **kwargs: answer({1: 0.95})
    )
    ok, note = judge_core.check_key(settings)

    assert ok is True
    assert "0.95" in note
    assert judge_core._JUDGE is None


def test_check_key_without_key(judge_settings):
    ok, note = judge_core.check_key(judge_settings)

    assert ok is False
    assert "ключ" in note.lower()
