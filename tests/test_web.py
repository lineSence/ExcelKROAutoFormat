"""Тесты веб-слоя. Нужны pytest и httpx (requirements-dev.txt)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_pipeline import _break_cell_styles, _build_source, _drop_shared_strings

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Клиент со своей рабочей папкой: ничего не пишется в системные папки."""
    monkeypatch.setenv("TMP_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    for module in ("app.main", "app.config"):
        sys.modules.pop(module, None)
    import app.main as web

    return TestClient(web.app)


@pytest.fixture()
def source_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "ОхтаМоллСМА без форматирования.xlsx"
    _build_source(path)
    _drop_shared_strings(path)
    _break_cell_styles(path)
    return path.read_bytes()


def _upload(client: TestClient, name: str, data: bytes) -> str:
    """Отправляет файл и возвращает билет разбора.

    Ответ на загрузку — редирект на страницу хода работы, а сам разбор идёт
    фоновой задачей. Редирект не следуем, чтобы забрать билет из адреса.
    """
    answer = client.post(
        "/upload",
        files={"file": (name, data, XLSX_TYPE)},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    location = answer.headers["location"]
    assert location.startswith("/upload/progress/")
    return location.rsplit("/", 1)[-1]


def _wait_done(client: TestClient, ticket: str, timeout: float = 30.0) -> dict:
    """Ждёт конца фонового разбора и возвращает последнее состояние билета."""
    deadline = time.monotonic() + timeout
    while True:
        state = client.get(f"/upload/state/{ticket}").json()
        assert state["found"]
        if state["finished"] or state["error"]:
            return state
        assert time.monotonic() < deadline, "разбор не закончился вовремя"
        time.sleep(0.05)


def test_health(client: TestClient) -> None:
    answer = client.get("/health")
    assert answer.status_code == 200
    assert answer.json()["status"] == "ok"


def test_index_opens(client: TestClient) -> None:
    answer = client.get("/")
    assert answer.status_code == 200


def test_wrong_extension_is_rejected(client: TestClient) -> None:
    answer = client.post(
        "/upload",
        files={"file": ("сверка.txt", b"123", "text/plain")},
    )
    assert answer.status_code == 400
    assert ".xlsx" in answer.text


def test_upload_and_download(client: TestClient, source_bytes: bytes) -> None:
    ticket = _upload(client, "ОхтаМоллСМА без форматирования.xlsx", source_bytes)
    state = _wait_done(client, ticket)
    assert not state["error"]
    token = state["token"]

    import app.main as web

    assert token in web.RESULTS

    downloaded = client.get(f"/download/{token}")
    assert downloaded.status_code == 200
    assert downloaded.content[:2] == b"PK"

    assert client.post(f"/cleanup/{token}").status_code == 200
    assert token not in web.RESULTS
    assert client.get(f"/download/{token}").status_code == 404


def test_upload_progress_page(client: TestClient, source_bytes: bytes) -> None:
    """Страница хода работы: билет открывается, по концу ведёт на сверку."""
    import app.main as web

    ticket = _upload(client, "ОхтаМоллСМА без форматирования.xlsx", source_bytes)

    page = client.get(f"/upload/progress/{ticket}", follow_redirects=False)
    if page.status_code == 200:
        # Разбор ещё идёт: видны полоса и список этапов.
        assert "Разбор сверки" in page.text
    else:
        # Маленький файл мог разобраться ещё до первого захода на страницу.
        assert page.status_code == 303

    state = _wait_done(client, ticket)
    assert not state["error"]
    assert state["token"] in web.RESULTS

    done = client.get(f"/upload/progress/{ticket}", follow_redirects=False)
    assert done.status_code == 303
    assert done.headers["location"] == f"/result/{state['token']}"


def test_unknown_ticket_is_explained(client: TestClient) -> None:
    """Неизвестный билет — понятный текст, а не Internal server error."""
    page = client.get("/upload/progress/нет-такого-билета")
    assert page.status_code == 404
    assert "Загрузите файл заново" in page.text

    state = client.get("/upload/state/нет-такого-билета").json()
    assert state["found"] is False


def test_unsafe_filename_is_cleaned(client: TestClient, source_bytes: bytes) -> None:
    """Имя с путём не выводит запись из рабочей папки."""
    import app.main as web

    assert web._safe_name("../../secret.xlsx") == "secret.xlsx"
    assert web._safe_name("") == "input.xlsx"

    ticket = _upload(client, "../../ОхтаМоллСМА без цен.xlsx", source_bytes)
    state = _wait_done(client, ticket)
    assert not state["error"]

    result = web.RESULTS[state["token"]]
    assert result.output_path.parent.parent.name == "work"


def test_too_big_file_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as web

    monkeypatch.setattr(web.settings, "max_upload_mb", 0)
    answer = client.post(
        "/upload",
        files={"file": ("большой.xlsx", b"0" * 2048, XLSX_TYPE)},
    )
    assert answer.status_code == 400
    assert "МБ" in answer.text
