"""Точка входа службы «Сверка КРО»."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .deps import jobs, letters, logger
from .web.forms import safe_name
from .web.routers import ROUTERS

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        restored = letters.restore()
        letters.note_skipped()
        if restored:
            logger.info("Письма восстановлены: %s", restored)
    except Exception:  # noqa: BLE001
        logger.exception("Письма не восстановлены")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Сверка КРО", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
_safe_name = safe_name
RESULTS = jobs.results_view()
__all__ = ["RESULTS", "app", "create_app"]
