"""Проверки распознавания фото плюсующего товара.

Сеть не нужна: клиент GigaChat подменяется заглушкой.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.core import vision

ROWS = [
    {"row": 12, "name": "Жидкость Husky Mint 30мл", "diff": 2, "sum_before": 900},
    {"row": 18, "name": "Жидкость Husky Mango 30мл", "diff": -2, "sum_before": 900},
    {"row": 31, "name": "Картридж Elfbar Ice", "diff": 1, "sum_before": 500},
]


@dataclass
class FakeSettings:
    """Настройки программы в объёме, нужном модулю зрения.

    Файл переключателей лежит рядом с базой примеров, поэтому здесь задаётся
    тот же `train_store_path`, что и в настройках программы.
    """

    train_store_path: str
    type_words: tuple = field(default=("жидкость", "картридж"))


def make_settings(tmp_path) -> FakeSettings:
    return FakeSettings(train_store_path=str(tmp_path / "data" / "train-samples.jsonl"))


def items(settings) -> list:
    return vision.items_from_rows(ROWS, settings.type_words)


def photo(tmp_path, name: str = "photo.jpg", body: bytes = b"\xff\xd8\xff\xe0jpeg"):
    file = tmp_path / name
    file.write_bytes(body)
    return file


def turn_on(settings, **extra) -> dict:
    values = {"vision_enabled": True, "vision_api_key": "a2V5"}
    values.update(extra)
    return vision.save_config(settings, values)


class FakeClient(vision.GigaChatVision):
    """Клиент, который не ходит в сеть."""

    def __init__(self, answer: str = "", error: str = "") -> None:
        super().__init__(auth_key="a2V5")
        self.answer = answer
        self.error = error
        self.calls: list[str] = []

    def ask(self, image, mime, prompt):  # type: ignore[override]
        self.calls.append(prompt)
        return self.answer, self.error


def answer(**fields) -> str:
    payload = {"text": "", "candidates": [], "comment": ""}
    payload.update(fields)
    return json.dumps(payload, ensure_ascii=False)


def test_candidates_are_surplus_only(tmp_path):
    settings = make_settings(tmp_path)
    plus = vision.surplus_items(items(settings))
    assert [item.row for item in plus] == [12, 31]


def test_off_by_default(tmp_path):
    settings = make_settings(tmp_path)
    config = vision.load_config(settings)
    assert config["vision_enabled"] is False
    assert config["vision_model"] == "GigaChat-2"
    assert config["vision_scope"] == "GIGACHAT_API_PERS"
    assert vision.status(settings)["ready"] is False

    result = vision.recognize(photo(tmp_path), items(settings), settings)
    assert not result.ok
    assert "выключено" in result.error


def test_settings_and_key_saved_from_interface(tmp_path):
    settings = make_settings(tmp_path)
    config = turn_on(settings, vision_max_rows="25", vision_pause_seconds="1,5")
    assert config["vision_max_rows"] == 25
    assert config["vision_pause_seconds"] == 1.5

    # Пустое поле ключа означает «оставить как было».
    config = vision.save_config(settings, {"vision_api_key": ""})
    assert config["vision_api_key"] == "a2V5"

    state = vision.status(settings)
    assert state["ready"] is True
    assert state["key_tail"] == "…"

    assert vision.forget_key(settings)["vision_api_key"] == ""
    assert vision.check(settings)[0] is False


def test_model_answer_is_checked_and_cached(tmp_path):
    settings = make_settings(tmp_path)
    config = turn_on(settings)
    client = FakeClient(
        answer="```json\n"
        + answer(
            text="HUSKY MINT 30ml",
            candidates=[
                {"row": 12, "confidence": 0.8},
                # Строки 999 в сверке нет: её обязаны отбросить.
                {"row": 999, "confidence": 0.9},
            ],
        )
        + "\n```"
    )
    cache = vision.Cache(vision.cache_path(settings), 10)
    file = photo(tmp_path)

    result = vision.recognize(file, items(settings), settings, cache, config, client)
    assert result.ok, result.error
    assert [candidate.row for candidate in result.candidates] == [12]
    assert result.candidates[0].source == "модель"

    again = vision.recognize(file, items(settings), settings, cache, config, client)
    assert again.cached is True
    assert len(client.calls) == 1


def test_text_fallback_when_model_gives_no_row(tmp_path):
    settings = make_settings(tmp_path)
    config = turn_on(settings)
    client = FakeClient(answer=answer(text="Жидкость Husky Mint 30мл"))

    result = vision.recognize(
        photo(tmp_path), items(settings), settings, None, config, client
    )
    assert result.ok, result.error
    assert result.candidates
    assert result.candidates[0].row == 12
    assert result.candidates[0].source == "текст"


def test_error_is_reported_not_raised(tmp_path):
    settings = make_settings(tmp_path)
    config = turn_on(settings)
    client = FakeClient(error="GigaChat ответил ошибкой 429: лимит")

    result = vision.recognize(
        photo(tmp_path), items(settings), settings, None, config, client
    )
    assert not result.ok
    assert "429" in result.error


def test_gigachat_answer_is_parsed():
    text = vision._first_text({"choices": [{"message": {"content": "готово"}}]})
    assert text == "готово"
    assert vision._first_text({}) == ""


def test_api_version_is_remembered(tmp_path):
    settings = make_settings(tmp_path)
    turn_on(settings)
    vision._remember_scope(settings, "GIGACHAT_API_B2B")
    assert vision.load_config(settings)["vision_scope"] == "GIGACHAT_API_B2B"
    # Чужое значение настройку не портит.
    vision._remember_scope(settings, "НЕТ_ТАКОЙ")
    assert vision.load_config(settings)["vision_scope"] == "GIGACHAT_API_B2B"


def test_human_answers_are_remembered(tmp_path):
    settings = make_settings(tmp_path)
    turn_on(settings)
    vision.remember(
        settings,
        photo="photo.jpg",
        digest="abc",
        text="HUSKY MINT",
        row=12,
        name="Жидкость Husky Mint 30мл",
        picked=True,
        source="модель",
    )
    assert vision.status(settings)["samples"] == 1


def test_photo_names(tmp_path):
    assert vision.is_photo("IMG_0001.JPG")
    assert not vision.is_photo("скан.pdf")
