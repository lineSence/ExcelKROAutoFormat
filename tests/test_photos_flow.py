"""Ответы по снимкам писем доходят до страницы «Фото товара».

Сети нет: разбор снимков подменяется, шаблоны читаются с диска.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import letters as letters_service
from app.services.photos import show_answers


class FakeAnswer:
    """Ответ модели по одному снимку."""

    def __init__(self, photo: str, digest: str = "", rows: bool = True) -> None:
        self.photo = photo
        self.digest = digest
        self.candidates = [object()] if rows else []


class FakeResult:
    summary = {"warehouse": "ВыборгРебусПДВ"}


class FakeJob:
    def __init__(self) -> None:
        self.photos: list = []
        self.photo_rows: set[int] = set()
        self.mail_vision: dict = {}
        self.result = FakeResult()


class FakeStore:
    def __init__(self) -> None:
        self.items: list = []
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class FakeLetter:
    def __init__(self) -> None:
        self.uid = "10"
        self.photos = [object()]


def _no_side_effects(monkeypatch: pytest.MonkeyPatch, report: dict) -> None:
    monkeypatch.setattr(
        letters_service, "vision_items", lambda result, settings: [object()]
    )
    monkeypatch.setattr(letters_service, "photo_notes", lambda job, settings: (True, ""))
    monkeypatch.setattr(letters_service, "keep_answers", lambda job, report: 0)
    monkeypatch.setattr(
        letters_service.mail_vision,
        "recognize_letters",
        lambda *args, **kwargs: report,
    )


def test_show_answers_keeps_only_photos_with_rows() -> None:
    job = FakeJob()
    good = FakeAnswer("01-мальборо.jpg", "a")
    empty = FakeAnswer("02-пусто.jpg", "b", rows=False)

    assert show_answers(job, {"results": [good, empty]}) == 1
    assert [item.photo for item in job.photos] == ["01-мальборо.jpg"]


def test_show_answers_does_not_double_the_same_photo() -> None:
    job = FakeJob()
    show_answers(job, {"results": [FakeAnswer("01-мальборо.jpg", "a")]})

    assert show_answers(job, {"results": [FakeAnswer("01-мальборо.jpg", "a")]}) == 0
    assert len(job.photos) == 1


def test_mail_vision_for_puts_answers_on_photo_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фоновый разбор при обработке сверки даёт список на подтверждение."""
    job = FakeJob()
    report = {
        "results": [FakeAnswer("01-мальборо.jpg", "a")],
        "photos": 9,
        "named": 6,
        "note": "",
    }
    _no_side_effects(monkeypatch, report)
    monkeypatch.setattr(
        letters_service,
        "letters_for",
        lambda result, store: {"mine": [FakeLetter()], "others": [], "note": ""},
    )
    store = FakeStore()

    out = asyncio.run(
        letters_service.mail_vision_for(job, store, object(), lambda: object())
    )

    assert out["named"] == 6
    assert [item.photo for item in job.photos] == ["01-мальборо.jpg"]
    assert store.saved == 1


def test_letter_vision_puts_answers_on_photo_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кнопка «Отправить на разбор» ведёт себя так же."""
    job = FakeJob()
    report = {
        "results": [FakeAnswer("01-мальборо.jpg", "a")],
        "photos": 1,
        "named": 1,
        "note": "",
    }
    _no_side_effects(monkeypatch, report)
    monkeypatch.setattr(
        letters_service.mail_match, "fits", lambda warehouse, letter: (True, "")
    )

    ok, _note = asyncio.run(
        letters_service.letter_vision(
            job, FakeLetter(), FakeStore(), object(), lambda: object()
        )
    )

    assert ok
    assert [item.photo for item in job.photos] == ["01-мальборо.jpg"]


def test_service_message_is_shown_once() -> None:
    """Сообщение рисует только каркас: на странице сверки он был дважды."""
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    result = (ROOT / "app" / "templates" / "result.html").read_text(encoding="utf-8")

    assert base.count("alert-ok") == 1
    assert "alert-ok" not in result
