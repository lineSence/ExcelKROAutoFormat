"""Эмбеддинги имён: сетевой провайдер, кэш, предел запросов и настройки.

Сеть не трогается: единственное место запроса (`sender`) подменяется.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core import embed as embed_core
from app.core import runtime


def _answer(vectors: list[list[float]]) -> dict:
    return {"data": [{"embedding": vector, "index": index} for index, vector in enumerate(vectors)]}


def test_mask_key_показывает_только_хвост():
    assert embed_core.mask_key("sk-or-v1-abcdef") == "…cdef"
    assert embed_core.mask_key("") == ""


def test_сетевой_провайдер_считает_косинус_одним_запросом():
    calls: list[dict] = []

    def sender(payload: dict) -> dict:
        calls.append(payload)
        return _answer([[1.0, 0.0], [0.0, 1.0]])

    embedder = embed_core.RemoteEmbedder(api_key="k", sender=sender)
    assert embedder.cos("Мальборо", "Marlboro") == pytest.approx(0.0)
    assert len(calls) == 1
    assert calls[0]["input"] == ["мальборо", "marlboro"]


def test_известные_имена_берутся_из_кэша():
    calls: list[dict] = []

    def sender(payload: dict) -> dict:
        calls.append(payload)
        count = len(payload["input"])
        return _answer([[1.0, 0.0]] * count)

    embedder = embed_core.RemoteEmbedder(api_key="k", sender=sender)
    embedder.vector("Сыр Российский")
    embedder.vector("сыр российский")
    assert len(calls) == 1


def test_предел_запросов_останавливает_траты():
    def sender(payload: dict) -> dict:
        return _answer([[1.0, 0.0]] * len(payload["input"]))

    embedder = embed_core.RemoteEmbedder(api_key="k", max_requests=1, sender=sender)
    embedder.vector("первое имя")
    with pytest.raises(embed_core.EmbedLimit):
        embedder.vector("второе имя")


def test_без_ключа_сетевой_провайдер_не_включается():
    embed_core.forget_embedder()
    base = Settings.load()
    without = base.__class__(**{**base.__dict__, "embed_provider": "openrouter", "embed_api_key": ""})
    assert embed_core.load_embedder(without) is None

    embed_core.forget_embedder()
    with_key = base.__class__(
        **{**base.__dict__, "embed_provider": "openrouter", "embed_api_key": "sk-test"}
    )
    embedder = embed_core.load_embedder(with_key)
    assert isinstance(embedder, embed_core.RemoteEmbedder)
    embed_core.forget_embedder()


def test_настройки_из_интерфейса_накладываются_на_запрос(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    monkeypatch.setattr(runtime, "runtime_path", lambda settings: str(path))

    base = Settings.load()
    runtime.save_embed(
        base,
        {
            "embed_enabled": True,
            "embed_provider": "openrouter",
            "embed_api_key": "sk-test-1234",
            "embed_model": "qwen/qwen3-embedding-0.6b",
            "embed_max_requests": "5",
        },
    )

    applied = runtime.apply(base)
    assert applied.embed_enabled is True
    assert applied.embed_provider == "openrouter"
    assert applied.embed_api_key == "sk-test-1234"
    assert applied.embed_max_requests == 5

    status = runtime.embed_status(applied)
    assert status["provider"] == "openrouter"
    assert status["has_key"] is True
    assert status["key_tail"] == "…1234"
    # Ключ целиком на страницу не попадает.
    assert "sk-test-1234" not in str(status)

    # Пустое поле ключа — «оставить как было».
    runtime.save_embed(base, {"embed_enabled": True, "embed_api_key": ""})
    assert runtime.apply(base).embed_api_key == "sk-test-1234"

    runtime.forget_embed_key(base)
    assert runtime.apply(base).embed_api_key == ""


def test_падающий_эмбеддер_не_ломает_проверку():
    from app.core.verify import Verifier

    class Broken:
        def cos(self, first: str, second: str) -> float:
            raise embed_core.EmbedError("нет связи")

    verifier = Verifier(model=None, embedder=Broken())
    assert verifier._embed_cos("сыр", "сырок") is None
