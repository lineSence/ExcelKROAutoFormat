"""Машинные маршруты для программы-компаньона.

Страницы приложения отвечают HTML и редиректами — разбирать их
программе неудобно и ненадёжно. Здесь те же действия отдают JSON,
а файлы — потоком:

* `POST /api/companion/jobs` — принять сверку из папки, вернуть билет;
* `GET  /api/companion/jobs/{билет}` — ход разбора и токен готовой сверки;
* `GET  /api/companion/exports` — что человек отправил кнопкой на выгрузку;
* `GET  /api/companion/exports/{токен}/result` — готовый файл сверки;
* `GET  /api/companion/exports/{токен}/photos.zip` — архив снимков задания;
* `POST /api/companion/exports/{токен}/done` — выгружено, убрать из очереди;
* `POST /api/companion/exports/{токен}/failed` — выгрузка не удалась.

Решает всё равно человек: компаньон только довозит файл до сервера и
забирает то, что уже подтверждено на странице сверки. Подтверждать
пары и заявки через эти маршруты нельзя — и не будет можно.

Пароль здесь отдельно не спрашивается: доступ закрывает
`app/security.py`. При внешнем адресе нужен тот же Basic-вход, что и для
страниц, а через туннель на 127.0.0.1 вход не требуется. Заголовок
`Origin` компаньон не посылает, поэтому проверка источника его пропускает;
если трогаете `SecurityMiddleware`, сохраните это поведение.
"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ...core import progress
from ...core.guard import UploadTooLarge
from ...core.pipeline import work_dir
from ...deps import base, exports, jobs, letters, logger, settings, settings_for
from ...services.exports import photo_zip
from ...services.upload_job import UPLOAD_STAGES, run_upload_job
from ...state.tasks import background
from ..forms import is_on, mode, percent, safe_name
from ..messages import EMPTY_UPLOAD, EXPIRED, NO_TICKET
from ..uploads import save_upload

router = APIRouter(prefix="/api/companion")
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NO_EXPORT = "Эта сверка на выгрузку не отправлена."
NO_PHOTOS = "Снимков по этому магазину нет."


def _bad(message: str, status: int = 400) -> JSONResponse:
    """Ошибка по-русски: её покажет компаньон в подсказке у трея."""
    return JSONResponse({"ok": False, "error": message}, status_code=status)


@router.post("/jobs")
async def create_job(
    file: UploadFile = File(...),
    prev: UploadFile | None = File(default=None),
    strict: str | None = Form(default=None),
    verify: str | None = Form(default=None),
    logic: str | None = Form(default=None),
):
    """Принимает сверку от компаньона и сразу отдаёт билет.

    Разбор идёт фоновой задачей, как и для страницы `/upload`:
    компаньону нельзя держать запрос минутами.
    """
    await run_in_threadpool(jobs.drop_expired)
    current = base()
    strict_on = is_on(strict)
    verify_mode = mode(verify if verify is not None else current.verify_mode)
    logic_percent = percent(logic)
    name = safe_name(file.filename)
    if not name.lower().endswith(".xlsx"):
        return _bad("Нужен файл с расширением .xlsx.")
    folder = work_dir(settings)
    source = folder / name
    try:
        size = await save_upload(file, source, settings.max_upload_mb)
    except UploadTooLarge:
        shutil.rmtree(folder, ignore_errors=True)
        return _bad(f"Файл больше {settings.max_upload_mb} МБ.", 413)
    if not size:
        shutil.rmtree(folder, ignore_errors=True)
        return _bad(EMPTY_UPLOAD)
    prev_path = None
    prev_name = ""
    if prev is not None and (prev.filename or "").strip():
        prev_name = safe_name(prev.filename)
        if not prev_name.lower().endswith(".xlsx"):
            shutil.rmtree(folder, ignore_errors=True)
            return _bad("Предыдущая сверка должна быть файлом .xlsx.")
        prev_path = folder / f"prev-{prev_name}"
        try:
            prev_size = await save_upload(prev, prev_path, settings.max_upload_mb)
        except UploadTooLarge:
            shutil.rmtree(folder, ignore_errors=True)
            return _bad(f"Предыдущая сверка больше {settings.max_upload_mb} МБ.", 413)
        if not prev_size:
            prev_path, prev_name = None, ""
    ticket = progress.start(UPLOAD_STAGES)
    progress.done(ticket, "save", f"{name}, {size / 1024 / 1024:.1f} МБ")
    background(
        run_upload_job(
            ticket,
            source,
            name,
            strict_on,
            verify_mode,
            logic_percent,
            folder,
            prev_path,
            prev_name,
            jobs=jobs,
            letters=letters,
            settings=settings,
            settings_for=settings_for,
            base=base,
        )
    )
    logger.info("Компаньон прислал сверку: билет %s", ticket)
    return {"ok": True, "ticket": ticket, "name": name, "progress_url": f"/upload/progress/{ticket}"}


@router.get("/jobs/{ticket}")
def job_state(ticket: str):
    """Ход разбора. Забытый билет — 404, чтобы компаньон повторил заливку."""
    view = progress.view(ticket)
    if view is None:
        return JSONResponse({"ok": False, "found": False, "error": NO_TICKET}, status_code=404)
    token = str(view.get("token") or "")
    ready = bool(token) and token in jobs
    return {
        "ok": True,
        "found": True,
        "ready": ready,
        "result_url": f"/result/{token}" if ready else "",
        **view,
    }


@router.get("/exports")
def export_queue():
    """Очередь выгрузок. Умершие по сроку сверки из неё убираются."""
    exports.keep_alive(jobs.alive)
    return {"ok": True, "exports": exports.pending()}


@router.get("/exports/{token}/result")
def export_result(token: str):
    if exports.get(token) is None:
        raise HTTPException(status_code=404, detail=NO_EXPORT)
    job = jobs.alive(token)
    if job is None:
        exports.forget(token)
        raise HTTPException(status_code=404, detail=EXPIRED)
    exports.take(token)
    return FileResponse(
        path=job.result.output_path,
        filename=job.result.output_name,
        media_type=XLSX_TYPE,
    )


@router.get("/exports/{token}/photos.zip")
async def export_photos(token: str):
    if exports.get(token) is None:
        raise HTTPException(status_code=404, detail=NO_EXPORT)
    job = jobs.alive(token)
    if job is None:
        exports.forget(token)
        raise HTTPException(status_code=404, detail=EXPIRED)
    path = await run_in_threadpool(photo_zip, job, letters)
    if path is None:
        raise HTTPException(status_code=404, detail=NO_PHOTOS)
    return FileResponse(path=path, filename=path.name, media_type="application/zip")


@router.post("/exports/{token}/done")
def export_done(token: str, note: str | None = Form(default=None)):
    item = exports.done(token, str(note or ""))
    if item is None:
        return _bad(NO_EXPORT, 404)
    logger.info("Компаньон выгрузил сверку: %s", token)
    return {"ok": True, "status": item.status}


@router.post("/exports/{token}/failed")
def export_failed(token: str, note: str | None = Form(default=None)):
    item = exports.failed(token, str(note or ""))
    if item is None:
        return _bad(NO_EXPORT, 404)
    logger.warning("Компаньон не смог выгрузить %s: %s", token, item.note)
    return {"ok": True, "status": item.status}
