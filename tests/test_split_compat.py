from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_split_web_imports() -> None:
    from app.main import RESULTS, app, settings
    from app.web.routers import ROUTERS

    assert app.title == "Сверка КРО"
    assert settings is not None
    assert RESULTS is not None
    assert len(ROUTERS) == 8
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/health" in paths
    assert "/upload" in paths
    assert "/result/{token}" in paths
    assert "/vision" in paths
    assert "/mail" in paths
    assert "/refs" in paths
    assert "/training" in paths
    assert "/update" in paths


def test_split_form_compatibility() -> None:
    from app.web.forms import META_MULTI_FIELDS, mode, percent, safe_name

    assert mode("model") == "model"
    assert mode("llm") == "llm"
    assert mode("shadow") == "off"
    assert percent("87") == 87
    assert percent("999") == 100
    assert safe_name("../../secret.xlsx") == "secret.xlsx"
    assert META_MULTI_FIELDS == ("sellers", "seller_hours", "auditors")


def test_letter_store_uses_core_dataclasses(tmp_path: Path, monkeypatch) -> None:
    from app.state.letters import LetterStore

    settings = SimpleNamespace()
    letter = SimpleNamespace(
        uid="42",
        photos=[SimpleNamespace(path=str(tmp_path / "photo.jpg"))],
    )

    def fake_load(received_settings):
        assert received_settings is settings
        return [letter]

    def fake_remember(received_settings, fresh):
        assert received_settings is settings
        return list(fresh) + [letter]

    monkeypatch.setattr("app.state.letters.mail_store.load", fake_load)
    monkeypatch.setattr("app.state.letters.mail_store.remember", fake_remember)
    monkeypatch.setattr("app.state.letters.mail_store.save", lambda *args: None)

    store = LetterStore(settings)
    assert store.restore() == 1
    assert store.by_uid("42") is letter
    fresh = SimpleNamespace(uid="43", photos=[])
    assert store.remember([fresh]) == 1
    assert store.by_uid("43") is fresh


def test_job_replace_keeps_rebuilt_folder(tmp_path: Path) -> None:
    from app.state.jobs import JobStore

    folder = tmp_path / "job"
    folder.mkdir()
    old_output = folder / "old.xlsx"
    old_output.write_bytes(b"old")
    source = folder / "source.xlsx"
    source.write_bytes(b"source")
    new_output = folder / "new.xlsx"
    new_output.write_bytes(b"new")

    settings = SimpleNamespace(result_ttl_minutes=60)
    old_result = SimpleNamespace(
        output_path=old_output,
        output_name="old.xlsx",
        source_name="source.xlsx",
        source_path=source,
    )
    new_result = SimpleNamespace(
        output_path=new_output,
        output_name="new.xlsx",
        source_name="source.xlsx",
        source_path=source,
    )

    store = JobStore(settings)
    old = store.remember(old_result, False, "off", 100)
    old.photos.append(SimpleNamespace(photo="x"))
    old.photo_rows.add(12)

    new = store.replace(old.token, new_result, False, "off", 100)

    assert store.get(old.token) is None
    assert store.get(new.token) is new
    assert new_output.is_file()
    assert new.photo_rows == {12}
    assert len(new.photos) == 1


def test_photo_adapter_matches_core_signatures(monkeypatch) -> None:
    from app.services.photos import vision_items

    captured = {}

    def fake_items(rows, type_words):
        captured["rows"] = rows
        captured["type_words"] = type_words
        return ["ok"]

    monkeypatch.setattr("app.services.photos.vision_core.items_from_rows", fake_items)
    result = SimpleNamespace(clusters=[{"row": 1}])
    settings = SimpleNamespace(type_words=("чай", "кофе"))

    assert vision_items(result, settings) == ["ok"]
    assert captured == {"rows": [{"row": 1}], "type_words": ("чай", "кофе")}
