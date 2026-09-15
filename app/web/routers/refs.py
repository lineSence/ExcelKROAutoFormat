from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from ...core import claims as claims_book, refs_sync, runtime
from ...core.guard import UploadTooLarge
from ...deps import logger, settings
from .. import render
from ..forms import safe_name, is_on
from ..messages import GENERIC_ERROR
from ..uploads import save_upload

router = APIRouter()
BOOKS = ("planning", "schedule", "claims")


@router.get("/refs", response_class=HTMLResponse)
def refs(request: Request):
    return render.refs_page(request)


@router.post("/refs/upload", response_class=HTMLResponse)
async def refs_upload(
    request: Request,
    planning: UploadFile | None = File(default=None),
    schedule: UploadFile | None = File(default=None),
    claims: UploadFile | None = File(default=None),
):
    saved = []
    for field, item in zip(BOOKS, (planning, schedule, claims), strict=True):
        if item is None or not (item.filename or "").strip():
            continue
        name = safe_name(item.filename)
        if not name.lower().endswith(".xlsx"):
            return render.refs_page(request, error=f"{name}: нужен файл .xlsx.")
        target = claims_book.book_path(settings.refs_dir) if field == "claims" else refs_sync.book_target(settings.refs_dir, field)
        try:
            size = await save_upload(item, target, settings.max_upload_mb)
        except UploadTooLarge as error:
            return render.refs_page(request, error=str(error))
        if not size:
            return render.refs_page(request, error=f"{name}: файл пустой.")
        try:
            await run_in_threadpool(refs_sync.mark_upload, settings.refs_state_path, field, name)
        except Exception:
            logger.exception("Состояние справочников не обновлено")
        saved.append(name)
    if not saved:
        return render.refs_page(request, error="Файлы не выбраны.")
    claims_book.forget_cache()
    return render.refs_page(request, note="Загружено: " + ", ".join(saved))


@router.post("/refs/check", response_class=HTMLResponse)
async def refs_check(request: Request):
    try:
        note = await run_in_threadpool(refs_sync.measure, settings)
    except Exception:
        logger.exception("Проверка справочников не удалась")
        return render.refs_page(request, error=GENERIC_ERROR)
    return render.refs_page(request, note=str(note))


@router.post("/refs/settings", response_class=HTMLResponse)
async def refs_settings(request: Request):
    """Сохранить необязательное автоподтверждение без редактирования .env.

    Галочка включена по умолчанию. При выключении неточные результаты
    справочников снова требуют явного подтверждения на странице результата.
    """
    form = await request.form()
    enabled = is_on(form.get("auto_confirm"))
    runtime.set_flag("refs_auto_confirm", enabled, runtime.runtime_path(settings))
    return render.refs_page(
        request,
        note=(
            "Автоподтверждение распознанных параметров включено."
            if enabled
            else "Автоподтверждение распознанных параметров выключено."
        ),
    )


@router.post("/refs/clear", response_class=HTMLResponse)
async def refs_clear(request: Request):
    try:
        await run_in_threadpool(refs_sync.clear_fill_log, settings.refs_state_path)
    except Exception as error:
        return render.refs_page(request, error=str(error))
    return render.refs_page(request, note="Журнал очищен.")
