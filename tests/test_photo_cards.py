"""Карточки снимков архива и ответы «Да»/«Нет»."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import photo_cards
from app.state.jobs import Job


class Store:
    def __init__(self, letters):
        self.items = list(letters)
        self.saved = 0

    def by_uid(self, uid):
        return next((item for item in self.items if str(item.uid) == str(uid)), None)

    def save(self):
        self.saved += 1


def _photo(folder: Path, name: str):
    file = folder / name
    file.write_bytes(b"photo")
    return SimpleNamespace(name=name, path=str(file), title="")


def _answer(name: str, row: int, item_name: str):
    candidate = SimpleNamespace(row=row, name=item_name, source="model")
    return SimpleNamespace(
        photo=name,
        digest="d1" if row else "d2",
        text="текст с упаковки",
        candidates=[candidate] if row else [],
        best=candidate if row else None,
    )


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    first = _photo(tmp_path, "one.jpg")
    second = _photo(tmp_path, "two.jpg")
    letter = SimpleNamespace(uid="17", photos=[first, second])
    store = Store([letter])
    result = SimpleNamespace(summary={"warehouse": "Склад"}, output_path=tmp_path / "итог.xlsx")
    job = Job(token="t1", result=result)
    job.photos.append(_answer("one.jpg", 12, "Сок вишня"))
    job.photos.append(_answer("two.jpg", 0, ""))
    job.photo_rows.add(12)

    monkeypatch.setattr(photo_cards.mail_match, "split", lambda warehouse, items: {"mine": list(items)})
    monkeypatch.setattr(photo_cards.vision_core, "remember", lambda *args, **kwargs: None)
    monkeypatch.setattr(photo_cards, "photo_notes", lambda job, settings: (True, "Строк с фото: 1."))
    return job, store, SimpleNamespace(sheet_name="Лист1")


def test_cards_show_every_photo_of_the_archive(setup):
    job, store, _settings = setup
    cards = photo_cards.cards(job, store)
    assert [card["name"] for card in cards] == ["one.jpg", "two.jpg"]
    assert cards[0]["url"] == "/mail/photo/17/one.jpg"
    assert (cards[0]["row"], cards[0]["guess"]) == (12, "Сок вишня")
    # Снимка без предложенной строки тоже видно, кнопка «Да» для него закрыта.
    assert cards[1]["row"] == 0
    assert all(card["state"] == "" for card in cards)


def test_yes_confirms_the_row_of_the_model(setup):
    job, store, settings = setup
    ok, state, message = photo_cards.apply_answer(job, store, settings, "17/one.jpg", "yes")
    assert (ok, state) == (True, "yes")
    assert "12" in message
    assert 12 in job.photo_rows
    assert job.photo_answers["17/one.jpg"] == "yes"
    # Изменение уходит и в архив: файл письма получает имя узнанного товара.
    assert store.items[0].photos[0].title == "Сок вишня"
    assert store.saved == 1
    # Подтверждённый снимок из ручного подбора уходит.
    assert [card["state"] for card in photo_cards.cards(job, store)][0] == "yes"
    assert all(getattr(item, "photo", "") != "one.jpg" for item in photo_cards.manual_items(job, store))


def test_no_sends_the_photo_to_manual_pick(setup):
    job, store, settings = setup
    ok, state, _message = photo_cards.apply_answer(job, store, settings, "17/one.jpg", "no")
    assert (ok, state) == (True, "manual")
    # Пометка «нет фото» на строке остаётся, снимок из архива не пропадает.
    assert 12 not in job.photo_rows
    assert store.items[0].photos[0].title == ""
    manual = photo_cards.manual_items(job, store)
    assert [getattr(item, "photo", "") for item in manual] == ["one.jpg"]


def test_yes_without_a_row_is_refused(setup):
    job, store, settings = setup
    ok, state, message = photo_cards.apply_answer(job, store, settings, "17/two.jpg", "yes")
    assert (ok, state) == (False, "")
    assert message == photo_cards.NO_ROW
    assert job.photo_answers == {}


def test_unknown_card_does_not_break(setup):
    job, store, settings = setup
    ok, _state, message = photo_cards.apply_answer(job, store, settings, "17/нет.jpg", "no")
    assert ok is False
    assert message == photo_cards.NO_CARD
