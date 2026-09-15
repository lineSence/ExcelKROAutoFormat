from __future__ import annotations
import logging
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from ...core import mail as mail_core, runtime
from ...deps import base, jobs, letters, logger, settings
from ...services.letters import letter_vision
from .. import render
from ..forms import is_on, safe_name, whole_number
from ..messages import GENERIC_ERROR, NO_JOB_FOR_LETTER, NO_LETTER, NO_PHOTO, key_not_cleared, settings_not_saved
router = APIRouter()
@router.get("/mail", response_class=HTMLResponse)
def mail(request: Request, token: str = ""): return render.mail_page(request, token=token)
@router.post("/mail/settings")
async def mail_settings(request: Request, enabled: str | None = Form(default=None), host: str = Form(default=""), port: str = Form(default=""), ssl: str | None = Form(default=None), user: str = Form(default=""), password: str = Form(default=""), folder: str = Form(default=""), senders: str = Form(default=""), subject: str = Form(default=""), days_back: str = Form(default=""), max_letters: str = Form(default=""), max_photos: str = Form(default=""), max_photo_mb: str = Form(default=""), timeout: str = Form(default=""), keep_days: str = Form(default="")):
    fields = {"mail_enabled": is_on(enabled), "mail_host": host.strip(), "mail_port": whole_number(port, 0), "mail_ssl": is_on(ssl), "mail_user": user.strip(), "mail_folder": folder.strip(), "mail_senders": senders.strip(), "mail_subject": subject.strip(), "mail_days_back": whole_number(days_back, 0), "mail_max_letters": whole_number(max_letters, 0), "mail_max_photos": whole_number(max_photos, 0), "mail_max_photo_mb": whole_number(max_photo_mb, 0), "mail_timeout": whole_number(timeout, 0), "mail_keep_days": whole_number(keep_days, 0)}
    if password.strip(): fields["mail_password"] = password.strip()
    try: await run_in_threadpool(mail_core.save_config, fields, settings)
    except Exception as error:
        logger.exception("Настройки почты не сохранены"); return render.mail_page(request, error=settings_not_saved(error, runtime.runtime_path(settings)), status_code=500)
    return RedirectResponse(url="/mail", status_code=303)
@router.post("/mail/check", response_class=HTMLResponse)
async def mail_check(request: Request, token: str = Form(default="")):
    try: note = await run_in_threadpool(mail_core.check, base())
    except mail_core.MailError as error: return render.mail_page(request, error=str(error), status_code=502, token=token)
    except Exception: logger.exception("Проверка почты не удалась"); return render.mail_page(request, error=GENERIC_ERROR, status_code=500, token=token)
    return render.mail_page(request, message=str(note), token=token)
@router.post("/mail/fetch", response_class=HTMLResponse)
async def mail_fetch(request: Request, token: str = Form(default="")):
    try: fresh = await run_in_threadpool(mail_core.collect, base())
    except mail_core.MailError as error: return render.mail_page(request, error=str(error), status_code=502, token=token)
    except Exception: logger.exception("Письма не загружены"); return render.mail_page(request, error=GENERIC_ERROR, status_code=500, token=token)
    added = letters.remember(fresh); letters.note_skipped(); letters.save(); return render.mail_page(request, message=f"Новых писем: {added}.", token=token)
@router.post("/mail/parse/{uid}", response_class=HTMLResponse)
async def mail_parse(request: Request, uid: str, token: str = Form(default="")):
    job = jobs.alive(token)
    if job is None: return render.mail_page(request, error=NO_JOB_FOR_LETTER, status_code=400, token=token)
    letter = letters.by_uid(uid)
    if letter is None: return render.mail_page(request, error=NO_LETTER, status_code=404, token=token)
    ok, note = await letter_vision(job, letter, letters, settings, base)
    return render.mail_page(request, message=note, token=token) if ok else render.mail_page(request, error=note, status_code=400, token=token)
@router.post("/mail/clear", response_class=HTMLResponse)
async def mail_clear(request: Request, token: str = Form(default="")):
    try: await run_in_threadpool(letters.forget_all)
    except Exception as error: return render.mail_page(request, error=str(error), status_code=500, token=token)
    return render.mail_page(request, message="Письма удалены.", token=token)
@router.post("/mail/password/clear", response_class=HTMLResponse)
async def mail_password_clear(request: Request, token: str = Form(default="")):
    try: await run_in_threadpool(mail_core.forget_password, settings)
    except Exception as error: return render.mail_page(request, error=key_not_cleared("Пароль", error, runtime.runtime_path(settings)), status_code=500, token=token)
    return render.mail_page(request, message="Пароль удалён.", token=token)
@router.get("/mail/photo/{uid}/{name}")
def mail_photo(uid: str, name: str):
    root = mail_core.photos_dir(settings, uid)
    if not root.exists(): raise HTTPException(status_code=404, detail=NO_PHOTO)
    wanted = safe_name(name)
    for file in sorted(root.iterdir()):
        if file.is_file() and (file.name == wanted or file.name.split("-", 1)[-1] == wanted):
            if file.resolve().is_relative_to(root.resolve()): return FileResponse(path=file, filename=file.name)
    raise HTTPException(status_code=404, detail=NO_PHOTO)
@router.get("/mail/archive/{uid}")
def mail_archive(uid: str):
    letter = letters.by_uid(uid)
    if letter is None: raise HTTPException(status_code=404, detail=NO_LETTER)
    try: archive = mail_core.build_archive(settings, letter)
    except FileNotFoundError as error: raise HTTPException(status_code=404, detail=NO_PHOTO) from error
    return FileResponse(path=archive, filename=mail_core.archive_name(letter), media_type="application/zip")
