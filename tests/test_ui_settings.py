from pathlib import Path

from app.config import Settings
from app.core import runtime, update


def test_default_verify_mode_is_persistent(tmp_path):
    settings = Settings(train_store_path=str(tmp_path / "train.jsonl"))
    runtime.save_default_verify_mode(settings, "llm")
    loaded = runtime.apply(settings)
    assert loaded.verify_mode == "llm"


def test_invalid_default_verify_mode_is_rejected(tmp_path):
    settings = Settings(train_store_path=str(tmp_path / "train.jsonl"))
    try:
        runtime.save_default_verify_mode(settings, "garbage")
    except ValueError as error:
        assert "Неизвестный режим" in str(error)
    else:
        raise AssertionError("invalid mode must be rejected")


def test_branch_validation_rejects_traversal(monkeypatch):
    monkeypatch.setenv("APP_DIR", "/tmp/not-a-repo")
    assert update._valid_branch("fix/p0-hardening")
    assert not update._valid_branch("../main")
    assert not update._valid_branch("main..backup")
    assert not update._valid_branch("-main")
