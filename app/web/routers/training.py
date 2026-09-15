from __future__ import annotations

import shutil

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from ...core import embed as embed_core, judge as judge_core, runtime
from ...core.guard import UploadTooLarge
from ...core.learning import append_samples, clear_samples, train_model
from ...core.parse import ParseError
from ...core.pipeline import work_dir
from ...deps import base, logger, settings
from ...services.samples import read_samples
from .. import render
from ..forms import is_on, percent, safe_name, whole_number
from ..messages import EMPTY_UPLOAD, GENERIC_ERROR, key_not_cleared, settings_not_saved
from ..uploads import save_upload

router = APIRouter()


@router.get("/training", response_class=HTMLResponse)
def training(request: Request):
    return render.training_page(request)


@router.post("/training/samples", response_class=HTMLResponse)
async def training_samples(request: Request, file: UploadFile = File(...)):
    name = safe_name(file.filename)
    if not name.lower().endswith(".xlsx"):
        return render.training_page(request, error="Нужен файл .xlsx.", status_code=400)
    folder = work_dir(settings)
    source = folder / name
    try:
        size = await save_upload(file, source, settings.max_upload_mb)
        if not size:
            return render.training_page(request, error=EMPTY_UPLOAD, status_code=400)
        samples = await run_in_threadpool(read_samples, source, name, settings)
        if not samples:
            return render.training_page(
                request,
                error="В файле нет готовых примеров.",
                status_code=400,
            )
        report = await run_in_threadpool(
            append_samples,
            settings.train_store_path,
            samples,
        )
    except UploadTooLarge as error:
        return render.training_page(request, error=str(error), status_code=400)
    except ParseError as error:
        return render.training_page(request, error=str(error), status_code=400)
    except Exception:  # noqa: BLE001
        logger.exception("Примеры не загружены")
        return render.training_page(request, error=GENERIC_ERROR, status_code=500)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return render.training_page(
        request,
        message=(
            f"Добавлено примеров: {int(report.get('added') or 0)}. "
            f"Всего в базе: {int(report.get('total') or 0)}."
        ),
    )


@router.post("/training/train", response_class=HTMLResponse)
async def training_train(request: Request):
    try:
        report = await run_in_threadpool(train_model, settings)
    except Exception as error:  # noqa: BLE001
        logger.exception("Модель не обучена")
        return render.training_page(request, error=str(error), status_code=400)
    return render.training_page(request, message="Модель обновлена.", report=report)


@router.post("/training/embed")
async def training_embed(
    request: Request,
    embed: str | None = Form(default=None),
    provider: str = Form(default=""),
    api_key: str = Form(default=""),
    model: str = Form(default=""),
    timeout: str = Form(default=""),
    max_requests: str = Form(default=""),
):
    fields = {
        "embed_enabled": is_on(embed),
        "embed_provider": provider.strip(),
        "embed_model": model.strip(),
        "embed_timeout": whole_number(timeout, 0),
        "embed_max_requests": whole_number(max_requests, 0),
    }
    if api_key.strip():
        fields["embed_api_key"] = api_key.strip()
    try:
        await run_in_threadpool(runtime.save_embed, settings, fields)
    except Exception as error:  # noqa: BLE001
        logger.exception("Настройки эмбеддингов не сохранены")
        return render.training_page(
            request,
            error=settings_not_saved(error, runtime.runtime_path(settings)),
            status_code=500,
        )
    return RedirectResponse(url="/training", status_code=303)


@router.post("/training/embed/check", response_class=HTMLResponse)
async def training_embed_check(request: Request):
    try:
        note = await run_in_threadpool(embed_core.check_remote, base())
    except Exception as error:  # noqa: BLE001
        logger.exception("Проверка эмбеддингов не удалась")
        return render.training_page(request, error=str(error), status_code=502)
    return render.training_page(request, message=str(note))


@router.post("/training/embed/key/clear", response_class=HTMLResponse)
async def training_embed_key_clear(request: Request):
    try:
        await run_in_threadpool(runtime.forget_embed_key, settings)
    except Exception as error:  # noqa: BLE001
        logger.exception("Ключ эмбеддингов не удалён")
        return render.training_page(
            request,
            error=key_not_cleared("Ключ", error, runtime.runtime_path(settings)),
            status_code=500,
        )
    return render.training_page(request, message="Ключ удалён.")


@router.post("/training/judge")
async def training_judge(
    request: Request,
    provider: str = Form(default=""),
    api_key: str = Form(default=""),
    model: str = Form(default=""),
    api_url: str = Form(default=""),
    timeout: str = Form(default=""),
    retries: str = Form(default=""),
    max_requests: str = Form(default=""),
    batch: str = Form(default=""),
    max_pairs: str = Form(default=""),
    logic: str = Form(default=""),
):
    fields = {
        "judge_provider": provider.strip(),
        "judge_model": model.strip(),
        "judge_api_url": api_url.strip(),
        "judge_timeout": whole_number(timeout, 0),
        "judge_retries": whole_number(retries, 0),
        "judge_max_requests": whole_number(max_requests, 0),
        "judge_batch": whole_number(batch, 0),
        "judge_max_pairs": whole_number(max_pairs, 0),
        "logic_weight": percent(logic) / 100,
    }
    if api_key.strip():
        fields["judge_api_key"] = api_key.strip()
    try:
        await run_in_threadpool(runtime.save_judge, settings, fields)
    except Exception as error:  # noqa: BLE001
        logger.exception("Настройки судьи не сохранены")
        return render.training_page(
            request,
            error=settings_not_saved(error, runtime.runtime_path(settings)),
            status_code=500,
        )
    return RedirectResponse(url="/training", status_code=303)


@router.post("/training/judge/check", response_class=HTMLResponse)
async def training_judge_check(request: Request):
    try:
        note = await run_in_threadpool(judge_core.check_key, base())
    except Exception as error:  # noqa: BLE001
        logger.exception("Проверка судьи не удалась")
        return render.training_page(request, error=str(error), status_code=502)
    return render.training_page(request, message=str(note))


@router.post("/training/judge/key/clear", response_class=HTMLResponse)
async def training_judge_key_clear(request: Request):
    try:
        await run_in_threadpool(runtime.forget_judge_key, settings)
    except Exception as error:  # noqa: BLE001
        logger.exception("Ключ судьи не удалён")
        return render.training_page(
            request,
            error=key_not_cleared("Ключ", error, runtime.runtime_path(settings)),
            status_code=500,
        )
    return render.training_page(request, message="Ключ удалён.")


@router.post("/training/clear")
async def training_clear(request: Request):
    try:
        await run_in_threadpool(clear_samples, settings)
    except Exception as error:  # noqa: BLE001
        logger.exception("База примеров не очищена")
        return render.training_page(request, error=str(error), status_code=500)
    return RedirectResponse(url="/training", status_code=303)
