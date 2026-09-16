"""Карточки снимков: «Да», ручной выбор товара из списка и «Не товар»."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import photo_cards


class Store:
    """Память писем: только то, что нужно карточкам."""

    def __init__(self, items):
        self.items = list(items)
        self.saved = 0

    def by_uid(self, uid):
        for letter in self.items:
            if str(letter.uid) == str(uid):
                return letter
        return None

    def save(self):
        self.saved += 1


def _photo(folder: Path, name: str, title: str = ""):
    path = folder / name
    path.write_bytes(b"\xff\xd8\xff")
    return SimpleNamespace(name=name, path=str(path), title=title)


def _answer(name: str, row: int, item_name: str):
    best = SimpleNamespace(row=row, name=item_name, source="модель")
    return SimpleNamespace(
        photo=name,
        digest=f"d-{name}",
        text=item_name,
        best=best,
        candidates=[best],
    )


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    folder = tmp_path / "17"
    folder.mkdir()
    letter = SimpleNamespace(
        uid="17",
        photos=[_photo(folder, "one.jpg", "IMG_0001"), _photo(folder, "two.jpg")],
    )
    store = Store([letter])
    job = SimpleNamespace(
        result=SimpleNamespace(summary={"warehouse": "Монпансье СМА"}, clusters=[]),
        photos=[_answer("one.jpg", 12, "Сок вишнёвый")],
        photo_rows=set(),
        photo_answers={},
    )
    items = [
        SimpleNamespace(row=12, name="Сок вишнёвый", diff=2.0),
        SimpleNamespace(row=15, name="Жидкость для мытья", diff=5.0),
    ]
    monkeypatch.setattr(
        photo_cards.mail_match,
        "split",
        lambda warehouse, letters: {"mine": list(letters), "others": []},
    )
    monkeypatch.setattr(
        photo_cards.vision_core, "items_from_rows", lambda rows, words: list(items)
    )
    monkeypatch.setattr(photo_cards.vision_core, "remember", lambda *a, **k: None)
    monkeypatch.setattr(photo_cards, "photo_notes", lambda job, settings: (True, "Строк с фото: 1."))
    monkeypatch.setattr(photo_cards, "drop_photo", lambda job, photo, digest: None)
    return SimpleNamespace(job=job, store=store, settings=SimpleNamespace(), letter=letter)


def test_cards_show_every_photo_of_the_archive(setup):
    cards = photo_cards.cards(setup.job, setup.store)
    assert [card["name"] for card in cards] == ["one.jpg", "two.jpg"]
    assert cards[0]["row"] == 12
    assert cards[1]["row"] == 0
    assert all(card["state"] == "" for card in cards)


def test_options_are_the_plus_items_of_the_reconciliation(setup):
    options = photo_cards.options(setup.job, setup.settings)
    assert [item["row"] for item in options] == [15, 12]
    assert options[0]["name"] == "Жидкость для мытья"


def test_yes_confirms_the_row_of_the_model(setup):
    ok, state, message = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/one.jpg", "yes"
    )
    assert ok and state == "yes"
    assert 12 in setup.job.photo_rows
    assert setup.letter.photos[0].title == "Сок вишнёвый"
    assert "12" in message


def test_pick_takes_the_row_chosen_by_hand(setup):
    ok, state, _ = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/two.jpg", "pick", 15
    )
    assert ok and state == "yes"
    assert setup.job.photo_rows == {15}
    assert setup.letter.photos[1].title == "Жидкость для мытья"


def test_not_an_item_keeps_the_name_from_the_letter(setup):
    setup.job.photo_rows.add(12)
    ok, state, _ = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/one.jpg", "pick", 0
    )
    assert ok and state == "notitem"
    # Строка не подтверждается, а имя вложения остаётся ровно как в письме.
    assert 12 not in setup.job.photo_rows
    assert setup.letter.photos[0].title == "IMG_0001"
    assert setup.store.saved == 0


def test_a_row_outside_the_reconciliation_is_refused(setup):
    ok, _, message = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/two.jpg", "pick", 99
    )
    assert not ok
    assert message == photo_cards.NO_PICK
    assert setup.job.photo_rows == set()


def test_yes_without_a_row_is_refused(setup):
    ok, _, message = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/two.jpg", "yes"
    )
    assert not ok
    assert message == photo_cards.NO_ROW


def test_unknown_card_does_not_break(setup):
    ok, _, message = photo_cards.apply_answer(
        setup.job, setup.store, setup.settings, "17/gone.jpg", "yes"
    )
    assert not ok
    assert message == photo_cards.NO_CARD
