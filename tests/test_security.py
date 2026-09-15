from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core import runtime
from app.security import install_security


def _client(host: str, user: str = "admin", password: str = "secret") -> TestClient:
    settings = Settings(app_host=host, web_auth_user=user, web_auth_password=password)
    app = FastAPI()
    install_security(app, settings)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/admin")
    def admin() -> dict:
        return {"ok": True}

    @app.post("/admin")
    def admin_post() -> dict:
        return {"ok": True}

    return TestClient(app)


def _basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def test_health_is_public_on_external_bind() -> None:
    client = _client("0.0.0.0")
    assert client.get("/health").status_code == 200


def test_external_bind_requires_basic_auth() -> None:
    client = _client("0.0.0.0")
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", headers={"Authorization": _basic("admin", "secret")}).status_code == 200


def test_external_bind_rejects_bad_basic_auth() -> None:
    client = _client("0.0.0.0")
    assert client.get("/admin", headers={"Authorization": _basic("admin", "wrong")}).status_code == 401


def test_csrf_source_is_checked_for_browser_posts() -> None:
    client = _client("0.0.0.0")
    headers = {
        "Authorization": _basic("admin", "secret"),
        "Origin": "https://evil.example",
        "Host": "127.0.0.1:8000",
    }
    assert client.post("/admin", headers=headers).status_code == 403


def test_same_origin_post_is_allowed() -> None:
    client = _client("0.0.0.0")
    headers = {
        "Authorization": _basic("admin", "secret"),
        "Origin": "http://testserver",
        "Host": "testserver",
    }
    assert client.post("/admin", headers=headers).status_code == 200


def test_runtime_writes_are_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    runtime.save({"judge_provider": "gigachat"}, path)
    assert path.read_text(encoding="utf-8").strip().startswith("{")
    assert (path.stat().st_mode & 0o777) == 0o600


def test_runtime_replaces_configured_provider_urls(tmp_path: Path) -> None:
    @dataclass
    class FakeSettings:
        train_store_path: str = str(tmp_path / "data" / "train-samples.jsonl")
        judge_api_url: str = ""
        embed_api_url: str = ""

    state_dir = tmp_path / "data"
    state_dir.mkdir()
    runtime.save(
        {
            "judge_api_url": "http://127.0.0.1:2375/secret",
            "embed_api_url": "http://169.254.169.254/latest/meta-data",
        },
        state_dir / "runtime.json",
    )
    result = runtime.apply(FakeSettings())
    assert result.judge_api_url.endswith("/chat/completions")
    assert result.embed_api_url.endswith("/embeddings")
