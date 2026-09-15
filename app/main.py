"""Точка входа службы «Сверка КРО».

Раньше здесь было больше двух тысяч строк: все обработчики, вся
память и все тексты. Теперь файл только собирает приложение из
роутеров, а работа живёт в `web`, `services` и `state`.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from . import __version__
from .deps import jobs,letters,logger,settings
from .web.forms import safe_name
from .web.routers import ROUTERS
@asynccontextmanager
async def lifespan(app:FastAPI):
    try:
        restored=letters.restore();letters.note_skipped()
        if restored:logger.info("Письма восстановлены: %s",restored)
    except Exception:logger.exception("Письма не восстановлены")
    yield
def create_app()->FastAPI:
    app=FastAPI(title="Сверка КРО",version=__version__,lifespan=lifespan)
    for router in ROUTERS:app.include_router(router)
    return app
app=create_app()
_safe_name=safe_name
RESULTS=jobs.results_view()
__all__=["RESULTS","app","create_app","settings"]
