"""Проверки приёма фото и сообщений ревизоров из почты.

Сеть не нужна: вместо почтового ящика подставляется заглушка IMAP через
аргумент `opener`, поэтому тесты идут без пароля и без интернета.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from app.core import mail

JPEG = b"\xff\xd8\xff\xe0jpeg"


@dataclass
class FakeSettings:
    """Настройки в объёме, нужном модулю почты.

    Файл переключателей и папка снимков выбираются по `train_store_path`,
    как и в настройках программы.
    """

    train_store_path: str


def make_settings(tmp_path) -> FakeSettings:
    return FakeSettings(train_store_path=str(tmp_path / "data" / "train-samples.jsonl"))


def turn_on(settings, **extra) -> dict:
    values = {
        "mail_enabled": True,
        "mail_login": "foto@firma.ru",
        "mail_password": "parol-prilozheniya",
        "mail_senders": "@firma.ru",
        "mail_only_unseen": False,
        "mail_since_days": 0,
    }
    values.update(extra)
    return mail.save_config(settings, values)


def letter(
    sender: str = "Ревизор <reviz@firma.ru>",
    subject: str = "Излишек, магазин 47",
    body: str = "По коду 1234567 излишек 3 шт, фото внутри.",
    photo: bytes | None = JPEG,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = "Mon, 14 Sep 2026 10:00:00 +0300"
    message.set_content(body)
    if photo is not None:
        message.add_attachment(
            photo, maintype="image", subtype="jpeg", filename="photo.jpg"
        )
    return message.as_bytes()


class FakeBox:
    """Ящик без сети: отдаёт заранее собранные письма."""

    def __init__(self, letters: dict[str, bytes]) -> None:
        self.letters = dict(letters)
        self.folder = ""
        self.marked: list[str] = []
        self.closed = False

    def select(self, name):
        self.folder = name
        return "OK", [str(len(self.letters)).encode("ascii")]

    def uid(self, command, *args):
        name = str(command).lower()
        if name == "search":
            return "OK", [" ".join(self.letters).encode("ascii")]
        if name == "fetch":
            return "OK", [(b"1 (RFC822)", self.letters.get(str(args[0]), b""))]
        if name == "store":
            self.marked.append(str(args[0]))
            return "OK", [b""]
        return "NO", []

    def logout(self):
        self.closed = True
        return "BYE", []


def test_off_by_default(tmp_path):
    settings = make_settings(tmp_path)
    config = mail.load_config(settings)
    assert config["mail_enabled"] is False
    assert config["mail_host"] == "imap.mail.ru"
    assert config["mail_port"] == 993

    state = mail.status(settings)
    assert state["ready"] is False
    assert "Выключен" in state["reason"]

    try:
        mail.collect(settings, opener=lambda config: FakeBox({}))
    except mail.MailError as error:
        assert "выключен" in str(error)
    else:  # pragma: no cover - защита от молчаливого приёма почты
        raise AssertionError("выключенный приём почты обязан отказать")


def test_settings_and_password_from_interface(tmp_path):
    settings = make_settings(tmp_path)
    config = turn_on(settings, mail_port="993", mail_timeout="15,5")
    assert config["mail_port"] == 993
    assert config["mail_timeout"] == 15.5

    # Пустое поле пароля означает «оставить как было».
    config = mail.save_config(settings, {"mail_password": ""})
    assert config["mail_password"] == "parol-prilozheniya"

    state = mail.status(settings)
    assert state["ready"] is True
    assert state["has_password"] is True
    # Пароль целиком в интерфейс не попадает.
    assert state["password_tail"] == "…niya"

    mail.forget_password(settings)
    assert mail.status(settings)["has_password"] is False


def test_hints_are_deterministic():
    hints = mail.read_hints(
        "По коду 1234567 пересорт, 3 шт, магазин 47", subject="Излишек"
    )
    assert hints["codes"] == ["1234567"]
    assert hints["quantity"] == "3"
    assert hints["store"] == "47"
    assert "пересорт" in hints["words"]
    assert "излишек" in hints["words"]


def test_allowed_senders():
    assert mail.allowed_sender("reviz@firma.ru", "") is True
    assert mail.allowed_sender("reviz@firma.ru", "@firma.ru") is True
    assert mail.allowed_sender("reviz@firma.ru", "reviz@firma.ru") is True
    assert mail.allowed_sender("spam@other.ru", "@firma.ru") is False


def test_attachment_names_are_cleaned():
    assert mail.safe_name("../../etc/passwd") == "passwd"
    assert mail.safe_name("") == "photo.jpg"
    assert mail.is_photo("IMG_0001.JPG")
    assert not mail.is_photo("акт.pdf")


def test_letter_is_parsed_with_photo():
    parsed = mail.parse_letter("7", letter(), 15)
    assert parsed.sender == "reviz@firma.ru"
    assert parsed.subject == "Излишек, магазин 47"
    assert [photo.name for photo in parsed.photos] == ["photo.jpg"]
    assert parsed.hints["codes"] == ["1234567"]


def test_big_attachment_is_reported_not_saved():
    parsed = mail.parse_letter("8", letter(photo=b"x" * 2 * 1024 * 1024), 1)
    assert parsed.photos == []
    assert parsed.notes and "больше 1 МБ" in parsed.notes[0]


def test_collect_saves_photos_and_skips_known_letters(tmp_path):
    settings = make_settings(tmp_path)
    turn_on(settings)
    box = FakeBox({"11": letter(), "12": letter(sender="spam@other.ru")})

    report = mail.collect(settings, opener=lambda config: box)
    assert report["photos"] == 1
    assert [item.uid for item in report["letters"]] == ["11"]
    assert report["skipped"] and "не в списке" in report["skipped"][0]
    assert box.folder == "INBOX"
    assert box.closed is True
    # Разобранное письмо помечается прочитанным в самом ящике.
    assert box.marked == ["11"]

    saved = Path(report["letters"][0].photos[0].path)
    assert saved.is_file()
    assert saved.read_bytes() == JPEG
    # Номер по порядку в имени файла нужен, чтобы снимки не перетирали друг друга.
    assert saved.name == "01-photo.jpg"
    assert saved.parent == mail.photos_dir(settings) / "11"

    # Повторный заход те же письма не тянет: их номера уже в mail-state.json.
    again = mail.collect(settings, opener=lambda config: box)
    assert again["letters"] == []
    assert again["photos"] == 0
    assert mail.status(settings)["seen"] == 2


def test_photo_limit_is_respected(tmp_path):
    settings = make_settings(tmp_path)
    turn_on(settings, mail_max_photos="1")
    box = FakeBox({"21": letter(), "22": letter()})

    report = mail.collect(settings, opener=lambda config: box)
    assert report["photos"] == 1
    notes = [note for item in report["letters"] for note in item.notes]
    assert any("предел" in note for note in notes)
