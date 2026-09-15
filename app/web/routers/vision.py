from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from ...core import runtime
from ...core import vision as vision_core
from ...core.guard import UploadTooLarge
from ...deps import base, jobs, logger, settings
from ...services.photos import drop_photo, keep_answers, photo_notes, vision_items
from .. import render
from ..forms import is_on, safe_name, whole_number
from ..messages import GENERIC_ERROR, NO_PHOTO, key_not_cleared, settings_not_saved
from ..uploads import save_upload

router = APIRouter()


@router.get("/vision", response_class=HTMLResponse)
def vision(request: Request, token: str = ""):
    return render.vision_page(request, token=token)


@router.post("/vision/settings", response_class=HTMLResponse)
async def vision_settings(
    request: Request,
    enabled: str | None = Form(default=None),
    api_key: str = Form(default=""),
    model: str = Form(default=""),
    max_rows: str = Form(default=""),
    max_photo_mb: str = Form(default=""),
    timeout: str = Form(default=""),
    retries: str = Form(default=""),
    pause_seconds: str = Form(default=""),
    text_min_score: str = Form(default=""),
    cache_limit: str = Form(default=""),
    token: str = Form(default=""),
):
    fields = {
        "vision_enabled": is_on(enabled),
        "vision_model": model.strip(),
        "vision_max_rows": whole_number(max_rows, 0),
        "vision_max_photo_mb": whole_number(max_photo_mb, 0),
        "vision_timeout": whole_number(timeout, 0),
        "vision_retries": whole_number(retries, 0),
        "vision_pause_seconds": pause_seconds.strip(),
        "vision_text_min_score": text_min_score.strip(),
        "vision_cache_limit": whole_number(cache_limit, 0),
    }
    if api_key.strip():
        fields["vision_api_key"] = api_key.strip()
    try:
        await run_in_threadpool(vision_core.save_config, settings, fields)
    except Exception as error:  # noqa: BLE001
        logger.exception("Настройки распознавания не сохранены")
        return render.vision_page(
            request,
            error=settings_not_saved(error, runtime.runtime_path(settings)),
            token=token,
            status_code=500,
        )
    return render.vision_page(request, message="Настройки сохранены.", token=token)


@router.post("/vision/photos", response_class=HTMLResponse)
async def vision_photos(
    request: Request,
    token: str = Form(...),
    photos: list[UploadFile] = File(default=[]),
):
    job = jobs.alive(token)
    if job is None:
        return render.vision_page(request, error="Сверка не найдена.", status_code=404)

    current = base()
    config = vision_core.load_config(current)
    if not config["vision_enabled"] or not config["vision_api_key"]:
        return render.vision_page(
            request,
            error="Сначала включите распознавание и введите ключ API.",
            token=token,
            status_code=400,
        )

    payload: list = []
    folder = job.result.output_path.parent / "photos"
    for item in photos:
        name = safe_name(item.filename)
        if not vision_core.is_photo(name):
            return render.vision_page(
                request,
                error=f"Файл {name} не похож на снимок: нужны jpg, png, webp или heic.",
                token=token,
                status_code=400,
            )
        target = folder / name
        try:
            size = await save_upload(item, target, int(config["vision_max_photo_mb"]))
        except UploadTooLarge:
            return render.vision_page(
                request,
                error=f"Снимок {name} больше {config['vision_max_photo_mb']} МБ.",
                token=token,
                status_code=400,
            )
        except OSError:
            logger.exception("Снимок не сохранён")
            return render.vision_page(request, error=GENERIC_ERROR, token=token, status_code=500)
        if size:
            payload.append(target)

    if not payload:
        return render.vision_page(request, error=NO_PHOTO, token=token, status_code=400)

    try:
        found = await run_in_threadpool(
            vision_core.recognize_all,
            payload,
            vision_items(job.result, current),
            current,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Снимки не разобраны")
        return render.vision_page(request, error=GENERIC_ERROR, token=token, status_code=500)

    job.photos[:] = list(found)
    return render.vision_page(
        request,
        message=f"Разобрано снимков: {len(found)}. Подтверждайте по одному: остальные останутся на странице.",
        results=job.photos,
        token=token,
    )


@router.post("/vision/confirm", response_class=HTMLResponse)
async def vision_confirm(
    request: Request,
    token: str = Form(...),
    photo: str = Form(default=""),
    digest: str = Form(default=""),
    text: str = Form(default=""),
    row: str = Form(default=""),
    name: str = Form(default=""),
    source: str = Form(default=""),
    picked: str = Form(default=""),
):
    job = jobs.alive(token)
    if job is None:
        return render.vision_page(request, error="Сверка не найдена.", status_code=404)

    number = whole_number(row, 0)
    chosen = is_on(picked) and number > 0
    try:
        await run_in_threadpool(
            vision_core.remember,
            settings,
            photo,
            digest,
            text,
            number if chosen else 0,
            name,
            chosen,
            source,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Ответ по снимку не сохранён")
        return render.vision_page(request, error=GENERIC_ERROR, token=token, status_code=500)

    if chosen:
        keep_answers(
            job,
            {
                "results": [
                    type(
                        "Answer",
                        (),
                        {"candidates": [type("Candidate", (), {"row": number})()],},
                    )()
                ]
            },
        )
    drop_photo(job, photo, digest)
    ok, detail = photo_notes(job, settings)
    warning = detail if not ok else ""
    if chosen:
        message = f"Ответ записан: строка {number}, {name}."
    else:
        message = "Ответ записан: ни одна из предложенных строк не подходит. Пометки «нет фото» остались на месте."
    return render.vision_page(request, message=message, error=warning, token=token)


@router.post("/vision/check", response_class=HTMLResponse)
async def vision_check(request: Request, token: str = Form(default="")):
    try:
        note = await run_in_threadpool(vision_core.check, base())
    except Exception as error:  # noqa: BLE001
        logger.exception("Проверка распознавания не удалась")
        return render.vision_page(request, error=str(error), token=token, status_code=502)
    if isinstance(note, tuple):
        ok, text = note
        return render.vision_page(
            request,
            message=str(text) if ok else "",
            error=str(text) if not ok else "",
            token=token,
            status_code=200 if ok else 400,
        )
    return render.vision_page(request, message=str(note), token=token)


@router.post("/vision/cache/clear", response_class=HTMLResponse)
async def vision_cache_clear(request: Request, token: str = Form(default="")):
    try:
        gone = await run_in_threadpool(vision_core.clear_cache, settings)
    except Exception as error:  # noqa: BLE001
        return render.vision_page(request, error=str(error), token=token, status_code=500)
    return render.vision_page(request, message=f"Кеш очищен, забыто снимков: {gone}.", token=token)


@router.post("/vision/key/clear", response_class=HTMLResponse)
async def vision_key_clear(request: Request, token: str = Form(default="")):
    try:
        await run_in_threadpool(vision_core.forget_key, settings)
    except Exception as error:  # noqa: BLE001
        return render.vision_page(
            request,
            error=key_not_cleared("Ключ", error, runtime.runtime_path(settings)),
            token=token,
            status_code=500,
        )
    return render.vision_page(request, message="Ключ удалён.", token=token)
