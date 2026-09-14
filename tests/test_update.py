"""Проверка готовности частей обновления (OTA)."""

from app.core import update


def _make_script(tmp_path, mode: int):
	"""Кладёт deploy/ota-update.sh с заданными правами."""
	deploy = tmp_path / "deploy"
	deploy.mkdir()
	script = deploy / "ota-update.sh"
	script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
	script.chmod(mode)
	return script


def test_script_without_exec_bit_is_ready(tmp_path, monkeypatch):
	"""Скрипт без права запуска годится: служба зовёт его через bash."""
	_make_script(tmp_path, 0o644)
	monkeypatch.setenv("APP_DIR", str(tmp_path))

	result = update.parts()

	assert result["script"] is True
	assert all("право запуска" not in item for item in result["missing"])


def test_missing_script_is_reported(tmp_path, monkeypatch):
	"""Если файла нет вовсе, страница должна об этом сказать."""
	monkeypatch.setenv("APP_DIR", str(tmp_path))

	result = update.parts()

	assert result["script"] is False
	assert result["ready"] is False
	assert any("ota-update.sh" in item for item in result["missing"])


def test_setup_command_uses_app_dir(tmp_path, monkeypatch):
	"""Команда доустановки указывает на настоящую папку программы."""
	monkeypatch.setenv("APP_DIR", str(tmp_path))

	assert str(tmp_path / "deploy" / "install-ota.sh") in update.setup_command()
