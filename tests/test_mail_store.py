"""Тесты памяти о письмах. Сети нет: работаем только с файлами."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import mail_store
from app.core.mail import Letter, Photo


class _Settings:
    """Заглушка настроек: путь к файлу подменяется в тесте."""


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Settings:
    stub = _Settings()
    monkeypatch.setattr(
        mail_store, "store_path", lambda _settings: tmp_path / "mail-letters.json"
    )
    return stub


def _letter(tmp_path: Path, uid: str = "7") -> Letter:
    folder = tmp_path / "mail-photos" / uid
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "01-photo.jpg"
    file.write_bytes(b"jpeg")
    return Letter(
        uid=uid,
        sender="revizor@example.com",
        subject="Фото излишков",
        day=dt.date(2026, 9, 14),
        text="Товар в списке несосчитанного",
        photos=[Photo(name="photo.jpg", path=str(file), size=4, title="Табак Свежий")],
        hints={"store": "1234", "in_list": True},
    )


def test_save_and_load_keeps_letter(tmp_path: Path, settings: _Settings) -> None:
    """После перезапуска письмо читается с диска целиком."""
    mail_store.save(settings, [_letter(tmp_path)])

    restored = mail_store.load(settings)

    assert len(restored) == 1
    kept = restored[0]
    assert kept.uid == "7"
    assert kept.subject == "Фото излишков"
    assert kept.day == dt.date(2026, 9, 14)
    assert kept.hints["store"] == "1234"
    assert kept.photos[0].title == "Табак Свежий"


def test_load_drops_letters_without_files(tmp_path: Path, settings: _Settings) -> None:
    """Срок хранения убрал снимки — кнопка архива бесполезна."""
    letter = _letter(tmp_path)
    mail_store.save(settings, [letter])
    Path(letter.photos[0].path).unlink()

    assert mail_store.load(settings) == []


def test_remember_merges_with_known(tmp_path: Path, settings: _Settings) -> None:
    """Свежие письма идут первыми, прежние не теряются."""
    mail_store.save(settings, [_letter(tmp_path, "7")])

    merged = mail_store.remember(settings, [_letter(tmp_path, "8")])

    assert [item.uid for item in merged] == ["8", "7"]
    assert [item.uid for item in mail_store.load(settings)] == ["8", "7"]


def test_remember_without_new_letters_keeps_old(
    tmp_path: Path, settings: _Settings
) -> None:
    """«Новых писем нет» не должно стирать кнопки архивов."""
    mail_store.save(settings, [_letter(tmp_path, "7")])

    assert [item.uid for item in mail_store.remember(settings, [])] == ["7"]


def test_load_without_file_is_empty(settings: _Settings) -> None:
    assert mail_store.load(settings) == []
