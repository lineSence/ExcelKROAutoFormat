from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from ...core import mail as mail_core, mail_store, runtime
from ...deps import base, jobs, letters, logger, settings
from ...services.letters import letter_vision
from .. import render
from ..forms import is_on, safe_name, whole_number
from ..messages import (
    GENERIC_ERROR,
    NO_JOB_FOR_LETTER,
    NO_LETTER,
    NO_PHOTO,
    key_not_cleared,
    settings_not_saved,
)

router = APIRouter()


@router.get("/mail", response_class=HTMLResponse)
def mail(request: Request, token: str = ""):
    return render.mail_page(request, token=token)


@router.post("/mail/settings")
async def mail_settings(
    request: Request,
    enabled: str | None = Form(default=None),
    host: str | None = Form(default=None),
    port: str | None = Form(default=None),
    login: str | None = Form(default=None),
    password: str | None = Form(default=None),
    folder: str | None = Form(default=None),
    senders: str | None = Form(default=None),
    since_days: str | None = Form(default=None),
    max_letters: str | None = Form(default=None),
    max_photos: str | None = Form(default=None),
    max_photo_mb: str | None = Form(default=None),
    timeout: str | None = Form(default=None),
    keep_days: str | None = Form(default=None),
    only_unseen: str | None = Form(default=None),
    mark_seen: str | None = Form(default=None),
):
    values: dict[str, object] = {
        "mail_enabled": is_on(enabled),
        "mail_only_unseen": is_on(only_unseen),
        "mail_mark_seen": is_on(mark_seen),
        "mail_host": host,
        "mail_port": port,
        "mail_login": login,
        "mail_password": password,
        "mail_folder": folder,
        "mail_senders": senders,
        "mail_since_days": since_days,
        "mail_max_letters": max_letters,
        "mail_max_photos": max_photos,
        "mail_max_photo_mb": max_photo_mb,
        "mail_timeout": timeout,
        "mail_keep_days": keep_days,
    }
    try:
        await run_in_threadpool(mail_core.save_config, settings, values)
        if senders is not None and not str(senders).strip():
            path = runtime.runtime_path(settings)
            stored = runtime.load(path)
            stored["mail_senders"] = ""
            runtime.save(stored, path)
    except OSError as error:
        logger.exception("Настройки почты не сохранены")
        return render.mail_page(
            request,
            error=settings_not_saved(error, runtime.runtime_path(settings)),
            status_code=500,
        )
    return RedirectResponse(url="/mail", status_code=303)


@router.post("/mail/check", response_class=HTMLResponse)
async def mail_check(request: Request, token: str = Form(default="")):
    try:
        ok, note = await run_in_threadpool(mail_core.check, settings)
    except mail_core.MailError as error:
        return render.mail_page(request, error=str(error), status_code=400, token=token)
    except Exception:  # noqa: BLE001
        logger.exception("Проверка почты не удалась")
        return render.mail_page(request, error=GENERIC_ERROR, status_code=500, token=token)
    return render.mail_page(
        request,
        message=str(note) if ok else "",
        error=str(note) if not ok else "",
        status_code=200 if ok else 400,
        token=token,
    )


@router.post("/mail/fetch", response_class=HTMLResponse)
async def mail_fetch(request: Request, token: str = Form(default="")):
    try:
        report = await run_in_threadpool(mail_core.collect, settings)
    except mail_core.MailError as error:
        return render.mail_page(request, error=str(error), status_code=400, token=token)
    except OSError as error:
        logger.exception("Снимки из почты не сохранены")
        return render.mail_page(request, error=f"Снимки не сохранены: {error}.", status_code=500, token=token)
    except Exception:  # noqa: BLE001
        logger.exception("Заход в почту не удался")
        return render.mail_page(request, error=GENERIC_ERROR, status_code=500, token=token)

    fresh = list(report.get("letters") or [])
    kept = await run_in_threadpool(mail_store.remember, settings, fresh)
    letters.items[:] = kept
    skipped = list(report.get("skipped") or [])
    letters.note_skipped(skipped)
    cleaned = int(report.get("cleaned") or 0)
    message = (
        f"Разобрано писем: {len(fresh)}, снимков скачано: {int(report.get('photos') or 0)}."
        if fresh
        else f"Новых писем нет. Уже сохранено писем: {len(kept)}."
    )
    if cleaned:
        message += f" Удалено старых снимков: {cleaned}."
    if skipped:
        message += f" Пропущено: {len(skipped)}."
    return render.mail_page(request, message=message, token=token)


@router.post("/mail/parse/{uid}", response_class=HTMLResponse)
async def mail_parse(request: Request, uid: str, token: str = Form(default="")):
    job = jobs.alive(token)
    if job is None:
        return render.mail_page(request, error=NO_JOB_FOR_LETTER, status_code=400, token=token)
    letter = letters.by_uid(uid)
    if letter is None:
        return render.mail_page(request, error=NO_LETTER, status_code=404, token=token)
    try:
        ok, note = await letter_vision(job, letter, letters, settings, base)
    except Exception:  # noqa: BLE001
        logger.exception("Письмо не отправлено на разбор")
        return render.mail_page(request, error=GENERIC_ERROR, status_code=500, token=token)
    return render.mail_page(
        request,
        message=note if ok else "",
        error="" if ok else note,
        status_code=200 if ok else 400,
        token=token,
    )


@router.post("/mail/clear", response_class=HTMLResponse)
async def mail_clear(request: Request, token: str = Form(default="")):
    try:
        report = await run_in_threadpool(letters.forget_all)
    except Exception as error:  # noqa: BLE001
        return render.mail_page(request, error=str(error), status_code=500, token=token)
    return render.mail_page(
        request,
        message=f"Письма удалены: память очищена, файлов на диске удалено {int(report.get('files') or 0)}.",
        token=token,
    )


@router.post("/mail/password/clear", response_class=HTMLResponse)
async def mail_password_clear(request: Request, token: str = Form(default="")):
    try:
        await run_in_threadpool(mail_core.forget_password, settings)
    except OSError as error:
        return render.mail_page(
            request,
            error=key_not_cleared("Пароль", error, runtime.runtime_path(settings)),
            status_code=500,
            token=token,
        )
    return render.mail_page(request, message="Пароль удалён.", token=token)


@router.get("/mail/photo/{uid}/{name}")
def mail_photo(uid: str, name: str):
    root = mail_core.photos_dir(settings)
    folder = root / mail_core.safe_name(uid)
    wanted = mail_core.safe_name(name)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=NO_PHOTO)
    for file in sorted(folder.iterdir()):
        if not file.is_file():
            continue
        if file.name == wanted or file.name.split("-", 1)[-1] == wanted:
            try:
                if file.resolve().is_relative_to(root.resolve()):
                    return FileResponse(path=file, filename=wanted)
            except (OSError, ValueError):
                pass
    raise HTTPException(status_code=404, detail=NO_PHOTO)


@router.get("/mail/archive/{uid}")
async def mail_archive(uid: str, token: str = ""):
    letter = letters.by_uid(uid)
    if letter is None:
        raise HTTPException(status_code=404, detail=NO_LETTER)
    if token:
        job = jobs.alive(token)
        if job is not None:
            try:
                await letter_vision(job, letter, letters, settings, base)
            except Exception:  # noqa: BLE001
                logger.exception("Перед сборкой архива снимки не разобраны")
    try:
        archive = await run_in_threadpool(mail_core.build_archive, settings, letter)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=NO_PHOTO) from error
    return FileResponse(
        path=archive,
        filename=mail_core.archive_name(letter),
        media_type="application/zip",
    )
