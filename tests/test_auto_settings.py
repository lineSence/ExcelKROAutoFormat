from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.core import runtime


def test_refs_auto_confirm_is_enabled_by_default() -> None:
    settings = Settings()
    assert settings.refs_auto_confirm is True


def test_runtime_can_disable_refs_auto_confirm(tmp_path: Path) -> None:
    @dataclass
    class FakeSettings:
        train_store_path: str = str(tmp_path / "data" / "train-samples.jsonl")
        refs_auto_confirm: bool = True
        refs_confirm_min_score: float = 0.95

    path = tmp_path / "data" / "runtime.json"
    runtime.save({"refs_auto_confirm": False}, path)
    settings = FakeSettings()
    runtime._CHOSEN.clear()
    runtime.save({"refs_auto_confirm": False}, runtime.runtime_path(settings))
    result = runtime.apply(settings)
    assert result.refs_auto_confirm is False
    assert result.refs_confirm_min_score == 0.95


def test_runtime_auto_confirm_relaxes_only_confirmation_gate(tmp_path: Path) -> None:
    @dataclass
    class FakeSettings:
        train_store_path: str = str(tmp_path / "data" / "train-samples.jsonl")
        refs_auto_confirm: bool = False
        refs_confirm_min_score: float = 0.95
        verify_mode: str = "off"

    settings = FakeSettings()
    runtime._CHOSEN.clear()
    runtime.save({"refs_auto_confirm": True}, runtime.runtime_path(settings))
    result = runtime.apply(settings)
    assert result.refs_auto_confirm is True
    assert result.refs_confirm_min_score == 0.0
    assert result.verify_mode == "off"
