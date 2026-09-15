"""Компаньон без сети: настройки, память и проход работы.

Сервер подменяется простым объектом: всё внешнее компаньон
получает аргументами, поэтому httpx для тестов не нужен.
"""

from __future__ import annotations

from pathlib import Path

from companion import state as state_module
from companion.config import Settings
from companion.state import Store
from companion.worker import Companion, digest_of, free_path, pick_files, settled


class FakeClient:
    """Сервер на бумаге: запоминает вызовы и отдаёт заготовленное."""

    def __init__(self, queue=None, view=None) -> None:
        self.queue = list(queue or [])
        self.view = dict(view or {})
        self.uploaded: list[str] = []
        self.finished: list[str] = []
        self.broken: list[str] = []

    def upload(self, path, prev=None):
        self.uploaded.append(Path(path).name)
        return {"ok": True, "ticket": "билет1"}

    def job(self, ticket):
        return dict(self.view)

    def exports(self):
        return list(self.queue)

    def fetch(self, token, kind, target):
        if kind == "photos.zip" and not self.queue:
            return None
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"data")
        return Path(target)

    def done(self, token, note=""):
        self.finished.append(token)
        return {"ok": True}

    def failed(self, token, note=""):
        self.broken.append(token)
        return {"ok": True}


def make_settings(tmp_path: Path) -> Settings:
    for name in ("вход", "выход", "сданное", "сбои"):
        (tmp_path / name).mkdir()
    return Settings(
        server_url="http://127.0.0.1:8000",
        inbox=str(tmp_path / "вход"),
        outbox=str(tmp_path / "выход"),
        archive=str(tmp_path / "сданное"),
        errors=str(tmp_path / "сбои"),
        stable_seconds=0,
        open_browser=False,
    )


def test_settings_survive_save_and_load(tmp_path):
    settings = make_settings(tmp_path)
    settings.save(tmp_path)
    again = Settings.load(tmp_path)
    assert again.inbox == settings.inbox
    assert again.stable_seconds == 0


def test_empty_password_keeps_old_one(tmp_path):
    settings = Settings(auth_user="ревизор", auth_password="секрет1234")
    fresh = settings.with_form({"auth_user": "другой", "auth_password": "   "})
    assert fresh.auth_password == "секрет1234"
    assert fresh.auth_user == "другой"
    assert settings.mask_password() == "…1234"[-4:] or settings.mask_password().startswith("…")


def test_numbers_and_flags_come_from_text(tmp_path):
    settings = Settings()
    fresh = settings.with_form({"poll_seconds": "45", "open_browser": "да", "strict": ""})
    assert fresh.poll_seconds == 45
    assert fresh.open_browser is True
    assert fresh.strict is False


def test_troubles_name_missing_settings():
    assert "Не указана папка со сверками из 1С." in Settings().troubles()


def test_open_excel_companion_files_are_skipped(tmp_path):
    folder = tmp_path / "вход"
    folder.mkdir()
    (folder / "Сверка.xlsx").write_bytes(b"x")
    (folder / "~$Сверка.xlsx").write_bytes(b"x")
    (folder / "заметка.txt").write_text("не сверка", encoding="utf-8")
    assert [path.name for path in pick_files(folder, 0)] == ["Сверка.xlsx"]


def test_fresh_file_waits_for_copy_to_finish(tmp_path):
    path = tmp_path / "Сверка.xlsx"
    path.write_bytes(b"x")
    assert settled(path, 60) is False
    assert settled(path, 0) is True


def test_same_file_is_not_sent_twice(tmp_path):
    settings = make_settings(tmp_path)
    source = Path(settings.inbox) / "Сверка.xlsx"
    source.write_bytes(b"сверка")
    client = FakeClient()
    store = Store(tmp_path)
    worker = Companion(settings, client, store)

    assert worker.send_new() == 1
    assert worker.send_new() == 0
    assert client.uploaded == ["Сверка.xlsx"]
    assert store.get(digest_of(source))["status"] == state_module.SENT


def test_ready_job_only_calls_the_person(tmp_path):
    settings = make_settings(tmp_path)
    source = Path(settings.inbox) / "Сверка.xlsx"
    source.write_bytes(b"сверка")
    client = FakeClient(view={"ok": True, "ready": True, "token": "токен1", "error": ""})
    store = Store(tmp_path)
    said: list[tuple[str, str]] = []
    worker = Companion(settings, client, store, notify=lambda title, text: said.append((title, text)))

    worker.send_new()
    assert worker.follow() == 1
    assert store.by_token("токен1")["status"] == state_module.READY
    # Сам по себе готовый разбор ничего не выгружает: решает человек.
    assert list(Path(settings.outbox).iterdir()) == []
    assert any("разобрана" in title for title, _text in said)


def test_button_export_lands_in_outbox(tmp_path):
    settings = make_settings(tmp_path)
    source = Path(settings.inbox) / "Сверка.xlsx"
    source.write_bytes(b"сверка")
    client = FakeClient(
        view={"ok": True, "ready": True, "token": "токен1", "error": ""},
        queue=[{"token": "токен1", "name": "Сверка готовая.xlsx", "status": "ждёт"}],
    )
    store = Store(tmp_path)
    worker = Companion(settings, client, store)

    worker.send_new()
    worker.follow()
    assert worker.take_exports() == 1

    names = sorted(path.name for path in Path(settings.outbox).iterdir())
    assert names == ["Сверка готовая фото.zip", "Сверка 