"""Тесты связки писем со зрением. Сети нет: разбор снимков подменяется."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import mail_vision
from app.core.mail import Letter, Photo
from app.core.vision import Candidate, PhotoResult


class _Settings:
    """Заглушка настроек: модулю нужен только `type_words`."""

    type_words: tuple = ()


def _letter(tmp_path: Path, uid: str = "7") -> Letter:
    folder = tmp_path / "mail-photos" / uid
    folder.mkdir(parents=True)
    file = folder / "01-photo.jpg"
    file.write_bytes(b"jpeg")
    return Letter(uid=uid, photos=[Photo(name="photo.jpg", path=str(file))])


def _ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mail_vision.vision_core,
        "load_config",
        lambda settings: {"vision_enabled": True, "vision_api_key": "ключ"},
    )


def test_letter_photos_skips_missing_files(tmp_path: Path) -> None:
    letter = _letter(tmp_path)
    letter.photos.append(Photo(name="нет.jpg", path=str(tmp_path / "нет.jpg")))
    assert mail_vision.letter_photos(letter) == [Path(letter.photos[0].path)]


def test_recognize_names_photos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Снимок письма получает имя товара, а ответ модели уходит наружу."""
    letter = _letter(tmp_path)
    answer = PhotoResult(photo="01-photo.jpg", digest="abc")
    answer.candidates = [Candidate(row=12, name="Табак Свежий", diff=2, price=100)]
    _ready(monkeypatch)

    report = mail_vision.recognize_letters(
        [letter],
        [object()],
        _Settings(),
        recognizer=lambda paths, items, settings: [answer],
    )

    assert report["letters"] == 1
    assert report["photos"] == 1
    assert report["named"] == 1
    assert letter.photos[0].title == "Табак Свежий"
    assert report["results"] == [answer]
    assert mail_vision.named(letter) == 1
    assert "узнано товаров: 1" in mail_vision.summary(report)


def test_recognize_without_key_explains_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = _letter(tmp_path)
    monkeypatch.setattr(
        mail_vision.vision_core,
        "load_config",
        lambda settings: {"vision_enabled": False, "vision_api_key": ""},
    )

    report = mail_vision.recognize_letters([letter], [object()], _Settings())

    assert report["photos"] == 0
    assert "ключ" in report["note"]
    assert letter.photos[0].title == ""
    assert mail_vision.summary(report) == report["note"]


def test_recognize_without_surplus_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = _letter(tmp_path)
    _ready(monkeypatch)

    report = mail_vision.recognize_letters([letter], [], _Settings())

    assert report["photos"] == 0
    assert "излишк" in report["note"]


def test_recognize_survives_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Беда со связью не роняет обработку сверки."""
    letter = _letter(tmp_path)
    _ready(monkeypatch)

    def boom(paths, items, settings):
        raise RuntimeError("нет связи")

    report = mail_vision.recognize_letters(
        [letter], [object()], _Settings(), recognizer=boom
    )

    assert report["error"]
    assert report["named"] == 0
    assert letter.photos[0].title == ""


def test_recognize_skips_named_letters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письмо, все снимки которого уже названы, в модель второй раз не уходит."""
    letter = _letter(tmp_path)
    letter.photos[0].title = "Табак Свежий"
    _ready(monkeypatch)

    def fail(paths, items, settings):
        raise AssertionError("названный снимок не должен уходить в модель")

    report = mail_vision.recognize_letters(
        [letter], [object()], _Settings(), recognizer=fail
    )

    assert report["photos"] == 0
    assert report["skipped"] == 1
    assert "разобрала раньше" in report["note"]


def test_recognize_limit_leaves_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """За один заход разбирается не больше предела, остаток считается."""
    _ready(monkeypatch)
    sent: list[int] = []

    def fake(paths, items, settings):
        sent.append(len(paths))
        return []

    report = mail_vision.recognize_letters(
        [_letter(tmp_path, "7"), _letter(tmp_path, "8")],
        [object()],
        _Settings(),
        recognizer=fake,
        limit=1,
    )

    assert report["photos"] == 1
    assert report["left"] == 1
    assert sent == [1]
    assert "Писем осталось: 1" in mail_vision.summary(report)


def test_recognize_button_reruns_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопка «Отправить на разбор» снимает пределы: письмо выбрал человек."""
    letter = _letter(tmp_path)
    letter.photos[0].title = "Старое имя"
    _ready(monkeypatch)
    answer = PhotoResult(photo="01-photo.jpg", digest="abc")
    answer.candidates = [Candidate(row=12, name="Табак Свежий", diff=2, price=100)]

    report = mail_vision.recognize_letters(
        [letter],
        [object()],
        _Settings(),
        recognizer=lambda paths, items, settings: [answer],
        only_new=False,
        limit=0,
    )

    assert report["photos"] == 1
    assert report["named"] == 1
    assert letter.photos[0].title == "Табак Свежий"
