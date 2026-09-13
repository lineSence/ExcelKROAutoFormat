"""Тесты распознавания фото: сеть не нужна, клиент модели подменяется."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import resort, vision

TYPE_WORDS = ("сигареты", "стики", "жидкость")


@dataclass
class FakeSettings:
    """Заменяет `Settings`: модулю нужны только эти два поля."""

    train_store_path: str
    type_words: tuple[str, ...] = field(default=TYPE_WORDS)


@pytest.fixture()
def settings(tmp_path: Path) -> FakeSettings:
    return FakeSettings(train_store_path=str(tmp_path / "data" / "train-samples.jsonl"))


def _items() -> list[resort.Item]:
    return [
        resort.make_item(12, "Сигареты Marlboro Gold", 3, 900, TYPE_WORDS),
        resort.make_item(31, "Стики HEETS Amber", 1, 220, TYPE_WORDS),
        resort.make_item(44, "Жидкость Husky Ice", -2, -400, TYPE_WORDS),
    ]


def _photo(tmp_path: Path) -> Path:
    path = tmp_path / "товар.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 not a real photo")
    return path


def test_surplus_only(settings: FakeSettings) -> None:
    """Кандидатами становятся только излишки, крупные первыми."""
    rows = [item.row for item in vision.surplus_items(_items())]
    assert rows == [12, 31]


def test_config_is_saved_in_interface(settings: FakeSettings) -> None:
    """Настройки и ключ хранятся в файле переключателей, а не в .env."""
    assert vision.load_config(settings)["vision_enabled"] is False

    config = vision.save_config(
        settings,
        {"vision_enabled": True, "vision_api_key": "key-1234", "vision_max_rows": "7"},
    )
    assert config["vision_enabled"] is True
    assert config["vision_max_rows"] == 7

    # Пустое поле ключа не стирает сохранённый ключ.
    config = vision.save_config(settings, {"vision_api_key": ""})
    assert config["vision_api_key"] == "key-1234"
    assert vision.status(settings)["key_tail"] == "…1234"

    assert vision.forget_key(settings)["vision_api_key"] == ""


def test_off_by_default(settings: FakeSettings, tmp_path: Path) -> None:
    """Выключенное распознавание не бросает исключений."""
    result = vision.recognize(_photo(tmp_path), _items(), settings)
    assert not result.ok
    assert "выключено" in result.error


def test_model_answer_is_checked(
    settings: FakeSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строка не из списка кандидатов отбрасывается, ответ кешируется."""
    vision.save_config(settings, {"vision_enabled": True, "vision_api_key": "key"})

    calls: list[str] = []

    def fake_ask(self, image: bytes, mime: str, prompt: str) -> tuple[str, str]:
        calls.append(mime)
        answer = {
            "text": "HEETS Amber Selection",
            "candidates": [
                {"row": 31, "confidence": 0.9},
                {"row": 999, "confidence": 0.8},
                {"row": 44, "confidence": 0.7},
            ],
            "comment": "",
        }
        return "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```", ""

    monkeypatch.setattr(vision.GeminiVision, "ask", fake_ask)

    photo = _photo(tmp_path)
    results = vision.recognize_all([photo], _items(), settings)
    assert len(results) == 1
    result = results[0]
    assert result.ok
    assert result.text == "HEETS Amber Selection"
    # 999 нет в сверке, 44 — недостача: остаётся одна строка.
    assert [candidate.row for candidate in result.candidates] == [31]
    assert result.candidates[0].source == "модель"

    # Повтор того же снимка берётся из кеша: обращений к модели больше нет.
    again = vision.recognize_all([photo], _items(), settings)[0]
    assert again.cached
    assert len(calls) == 1
    assert vision.status(settings)["cached"] == 1
    assert vision.clear_cache(settings) == 1


def test_text_fallback(settings: FakeSettings) -> None:
    """Без номеров строк подбор идёт по прочитанному тексту."""
    answer = {"text": "Marlboro Gold", "candidates": []}
    chosen, text, _ = vision._build(answer, vision.surplus_items(_items()), TYPE_WORDS, 0.4)
    assert text == "Marlboro Gold"
    assert chosen and chosen[0].row == 12
    assert chosen[0].source == "текст"


def test_bad_answer_is_reported(
    settings: FakeSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой модели возвращается полем error, а не исключением."""
    vision.save_config(settings, {"vision_enabled": True, "vision_api_key": "key"})
    monkeypatch.setattr(
        vision.GeminiVision,
        "ask",
        lambda self, image, mime, prompt: ("", "Модель ответила ошибкой 429: лимит"),
    )
    result = vision.recognize(_photo(tmp_path), _items(), settings)
    assert not result.ok
    assert "429" in result.error


def test_answers_are_remembered(settings: FakeSettings) -> None:
    """Ответ человека ложится в базу примеров по фото."""
    vision.remember(
        settings,
        photo="товар.jpg",
        digest="abc",
        text="HEETS Amber",
        row=31,
        name="Стики HEETS Amber",
        picked=True,
        source="модель",
    )
    path = vision.samples_path(settings)
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["row"] == 31
    assert record["picked"] is True
    assert vision.status(settings)["samples"] == 1
