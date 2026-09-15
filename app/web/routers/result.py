from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...core.meta import SheetMeta
from ...deps import base, jobs, letters, logger, settings, settings_for
from ...services.samples import store_answers
from ...services.upload_job import RebuildFailed, rebuild
from .. import render
from ..forms import CLAIM_PREFIX, DEFAULT_LOGIC, PAIR_PREFIX, is_on, mode, percent
from ..messages import EXPIRED

router = APIRouter()
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _answers(form, prefix):
    return {key[len(prefix):]: is_on(value) for key, value in form.multi_items() if key.startswith(prefix)}


@router.get("/result/{token}", response_class=HTMLResponse)
def result(request: Request, token: str):
    job = jobs.alive(token)
    if job is None:
        return render.error_page(request, EXPIRED, status=404)
    return render.result_page(request, job)


@router.post("/meta/{token}", response_class=HTMLResponse)
async def meta(request: Request, token: str):
    job = jobs.ready(token)
    if job is None:
        return render.error_page(request, EXPIRED, status=404)
    form = await request.form()
    strict = is_on(form.get("strict"))
    verify = mode(form.get("verify"))
    logic = percent(form.get("logic"), job.logic)
    sheet_meta = SheetMeta.from_form(form)
    try:
        fresh = await rebuild(
            job,
            dict(job.result.decisions),
            sheet_meta,
            strict,
            verify,
            logic,
            claim_decisions=dict(job.result.claim_decisions),
            jobs=jobs,
            letters=letters,
            settings=settings,
            settings_for=settings_for,
            base=base,
        )
    except RebuildFailed as error:
        return render.error_page(request, error.message, strict, verify, logic, error.status)
    logger.info("Шапка обновлена: %s", fresh.token)
    return render.result_page(request, fresh, "Шапка обновлена.")


@router.post("/meta/{token}/autosave", response_class=JSONResponse)
async def meta_autosave(request: Request, token: str):
    """Мгновенно сохранить изменённые ручные поля в текущей Job.

    Автосохранение намеренно не пересобирает Excel после каждого символа:
    браузер отправляет форму с задержкой только после того, как пользователь
    перестал печатать. Сам файл будет пересобран ближайшим штатным действием
    (подтверждением, заявкой и т. п.), используя уже сохранённый SheetMeta.
    """
    job = jobs.ready(token)
    if job is None:
        return JSONResponse({"ok": False, "error": EXPIRED}, status_code=404)
    form = await request.form()
    job.result.sheet_meta = SheetMeta.from_form(form)
    logger.debug("Автосохранение ручных полей: %s", token)
    return {"ok": True}


@router.post("/confirm/{token}", response_class=HTMLResponse)
async def confirm(request: Request, token: str):
    job = jobs.ready(token)
    if job is None:
        return render.error_page(request, EXPIRED, status=404)
    form = await request.form()
    strict = is_on(form.get("strict"))
    verify = mode(form.get("verify"))
    logic = percent(form.get("logic"), job.logic)
    answers = _answers(form, PAIR_PREFIX)
    decisions = dict(job.result.decisions)
    decisions.update(answers)
    kept = store_answers(job.result, answers, settings)
    try:
        fresh = await rebuild(
            job,
            decisions,
            job.result.sheet_meta,
            strict,
            verify,
            logic,
            claim_decisions=dict(job.result.claim_decisions),
            jobs=jobs,
            letters=letters,
            settings=settings,
            settings_for=settings_for,
            base=base,
        )
    except RebuildFailed as error:
        return render.error_page(request, error.message, strict, verify, logic, error.status)
    note = f"Подтверждено пар: {len(answers)}."
    if kept:
        note += f" В базу примеров добавлено: {kept}."
    return render.result_page(request, fresh, note)


@router.post("/claims/{token}", response_class=HTMLResponse)
async def claims(request: Request, token: str):
    job = jobs.ready(token)
    if job is None:
        return render.error_page(request, EXPIRED, status=404)
    form = await request.form()
    strict = is_on(form.get("strict"))
    verify = mode(form.get("verify"))
    logic = percent(form.get("logic"), job.logic)
    answers = _answers(form, CLAIM_PREFIX)
    decisions = dict(job.result.claim_decisions)
    decisions.update(answers)
    try:
        fresh = await rebuild(
            job,
            dict(job.result.decisions),
            job.result.sheet_meta,
            strict,
            verify,
            logic,
            claim_decisions=decisions,
            jobs=jobs,
            letters=letters,
            settings=settings,
            settings_for=settings_for,
            base=base,
        )
    except RebuildFailed as error:
        return render.error_page(request, error.message, strict, verify, logic, error.status)
    return render.result_page(request, fresh, f"Заявки обновлены: {len(answers)}.")


@router.get("/download/{token}")
def download(token: str):
    job = jobs.alive(token)
    if job is None:
        raise HTTPException(status_code=404, detail="Файл уже удалён. Загрузите сверку заново.")
    return FileResponse(path=job.result.output_path, filename=job.result.output_name, media_type=XLSX_TYPE)


@router.post("/cleanup/{token}")
def cleanup(token: str) -> dict:
    jobs.forget(token)
    return {"status": "ok"}
