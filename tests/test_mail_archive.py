"""Архив письма: имя по дате письма и точная копия текста.

Сети и почты здесь нет: письмо собирается в памяти через `EmailMessage`,
как и в остальных проверках почты.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from dataclasses import dataclass
from email.message import EmailMessage

from app.core import mail, mail_archive, mail_store
from app.services.exports import archive_name as photos_archive_name

JPEG = b"\xff\xd8\xff\xe0jpeg"

# Текст письма нарочно с двойными пробелами, отступами и пустой строкой:
# в «Письмо.txt» он должен попасть ровно таким.
BODY = "Здравствуйте!\n\n  Код  1234567,  излишек 3 шт.\nНочной продавец: Петров Пётр\n"


@dataclass
class FakeSettings:
    train_store_path: str


def make_settings(tmp_path) -> FakeSettings:
    return FakeSettings(train_store_path=str(tmp_path / "data" / "train-samples.jsonl"))


def make_letter(body: str = BODY) -> mail.Letter:
    message = EmailMessage()
    message["From"] = "Ревизор <reviz@firma.ru>"
    message["Subject"] = "Излишек, магазин 47"
    message["Date"] = "Wed, 22 Jul 2026 16:08:04 +0300"
    message.set_content(body)
    message.add_attachment(JPEG, maintype="image", subtype="jpeg", filename="photo.jpg")
    return mail.parse_letter("7", message.as_bytes(), 15)


def test_archive_name_is_date_and_time_of_letter():
    letter = make_letter()
    assert mail_archive.archive_name(letter) == "22-07-2026_16-08-04.zip"
    # Прежнее имя вида «письмо-7-магазин-47-2026-07-22.zip» ушло.
    assert mail.archive_name(letter) == "22-07-2026_16-08-04.zip"


def test_archive_name_falls_back_to_day_then_to_now():
    letter = make_letter()
    letter.at = None
    letter.day = dt.date(2026, 7, 22)
    assert mail_archive.archive_name(letter) == "22-07-2026_00-00-00.zip"
    letter.day = None
    assert len(mail_archive.archive_name(letter)) == len("22-07-2026_16-08-04.zip")


def test_letter_file_is_exact_copy_of_mail_text():
    letter = make_letter()
    text = mail_archive.letter_text(letter)
    assert text == letter.raw_text
    # Точная копия: двойные пробелы, отступ и пустая строка на месте.
    assert "  Код  1234567,  излишек 3 шт." in text.replace("\r\n", "\n")
    assert "Здравствуйте!\n\n" in text.replace("\r\n", "\n")
    # Ни шапки, ни подсказок, ни перечня вложений в файле нет.
    for added in ("Письмо:", "От кого:", "Коды товара:", "Текст письма:", "Снимки в архиве:"):
        assert added not in text


def test_long_text_is_not_cut_for_the_archive():
    body = "строка письма про излишек\n" * 200
    letter = make_letter(body)
    # Страница показывает обрезанный текст, архив — целый.
    assert len(letter.text) <= mail.TEXT_LIMIT
    assert len(mail_archive.letter_text(letter)) > mail.TEXT_LIMIT
    assert mail_archive.letter_text(letter).count("строка письма про излишек") == 200


def test_page_text_is_still_tidy_and_hints_work():
    letter = make_letter()
    assert "  Код  1234567" not in letter.text
    assert letter.hints["codes"] == ["1234567"]
    assert letter.hints["seller"] == "Петров Пётр"


def test_build_archive_keeps_name_and_letter_file(tmp_path):
    settings = make_settings(tmp_path)
    letter = make_letter()
    photo = tmp_path / "01-photo.jpg"
    photo.write_bytes(JPEG)
    letter.photos[0].data = b""
    letter.photos[0].path = str(photo)
    letter.photos[0].title = "Молоко Домик в деревне"

    path = mail_archive.build_archive(settings, letter)

    assert path.name == "22-07-2026_16-08-04.zip"
    with zipfile.ZipFile(path) as pack:
        names = pack.namelist()
        stored = pack.read(mail_archive.LETTER_FILE).decode("utf-8")
    assert mail_archive.LETTER_FILE == "Письмо.txt"
    assert names[0] == "Письмо.txt"
    assert "01-Молоко Домик в деревне.jpg" in names
    assert stored == letter.raw_text


def test_memory_keeps_time_and_exact_text():
    letter = make_letter()
    back = mail_store.letter_from(mail_store.letter_json(letter))
    assert back.raw_text == letter.raw_text
    assert mail_archive.archive_name(back) == "22-07-2026_16-08-04.zip"


def test_photos_archive_takes_the_latest_letter_time():
    early = make_letter()
    early.at = dt.datetime(2026, 7, 22, 9, 0, 0)
    late = make_letter()
    late.at = dt.datetime(2026, 7, 22, 16, 8, 4)
    assert photos_archive_name([early, late]) == "22-07-2026_16-08-04.zip"
