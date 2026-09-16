from __future__ import annotations
import shutil
from fastapi import APIRouter,File,Form,Request,UploadFile
from fastapi.responses import HTMLResponse,RedirectResponse
from starlette.concurrency import run_in_threadpool
from ...core import progress
from ...core.guard import UploadTooLarge
from ...core.pipeline import work_dir
from ...deps import base,jobs,letters,logger,settings,settings_for,templates
from ...services.upload_job import UPLOAD_STAGES,run_upload_job
from ...state.tasks import background
from .. import render
from ..forms import is_on,mode,percent,safe_name
from ..messages import EMPTY_UPLOAD,GENERIC_ERROR,NO_TICKET
from ..uploads import save_upload
router=APIRouter()
@router.post("/upload",response_class=HTMLResponse)
async def upload(request:Request,file:UploadFile=File(...),prev:UploadFile|None=File(default=None),sellers:UploadFile|None=File(default=None),strict:str|None=Form(default=None),verify:str|None=Form(default=None),logic:str|None=Form(default=None)):
    await run_in_threadpool(jobs.drop_expired); strict_on=is_on(strict)
    current=base(); verify_mode=mode(verify if verify is not None else current.verify_mode); logic_percent=percent(logic); name=safe_name(file.filename)
    if not name.lower().endswith(".xlsx"):return render.error_page(request,"Нужен файл с расширением .xlsx.",strict_on,verify_mode,logic_percent)
    folder=work_dir(settings); source=folder/name
    try:size=await save_upload(file,source,settings.max_upload_mb)
    except UploadTooLarge:shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,f"Файл больше {settings.max_upload_mb} МБ.",strict_on,verify_mode,logic_percent)
    if not size:shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,EMPTY_UPLOAD,strict_on,verify_mode,logic_percent)
    prev_path=None;prev_name=""
    if prev is not None and(prev.filename or "").strip():
        prev_name=safe_name(prev.filename)
        if not prev_name.lower().endswith(".xlsx"):shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,"Предыдущая сверка должна быть файлом .xlsx.",strict_on,verify_mode,logic_percent)
        prev_path=folder/f"prev-{prev_name}"
        try:prev_size=await save_upload(prev,prev_path,settings.max_upload_mb)
        except UploadTooLarge:shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,f"Предыдущая сверка больше {settings.max_upload_mb} МБ.",strict_on,verify_mode,logic_percent)
        if not prev_size:prev_path,prev_name=None,""
    sellers_path=None;sellers_name=""
    if sellers is not None and(sellers.filename or "").strip():
        sellers_name=safe_name(sellers.filename)
        if not sellers_name.lower().endswith(".xlsx"):shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,"Файл продавцов должен быть файлом .xlsx.",strict_on,verify_mode,logic_percent)
        sellers_path=folder/f"sellers-{sellers_name}"
        try:sellers_size=await save_upload(sellers,sellers_path,settings.max_upload_mb)
        except UploadTooLarge:shutil.rmtree(folder,ignore_errors=True);return render.error_page(request,f"Файл продавцов больше {settings.max_upload_mb} МБ.",strict_on,verify_mode,logic_percent)
        if not sellers_size:sellers_path,sellers_name=None,""
    ticket=progress.start(UPLOAD_STAGES);progress.done(ticket,"save",f"{name}, {size/1024/1024:.1f} МБ")
    background(run_upload_job(ticket,source,name,strict_on,verify_mode,logic_percent,folder,prev_path,prev_name,jobs=jobs,letters=letters,settings=settings,settings_for=settings_for,base=base,sellers_path=sellers_path,sellers_name=sellers_name))
    logger.info("Загрузка принята: билет %s",ticket);return RedirectResponse(url=f"/upload/progress/{ticket}",status_code=303)
@router.get("/upload/progress/{ticket}",response_class=HTMLResponse)
def upload_progress(request:Request,ticket:str):
    job=progress.view(ticket)
    if job is None:return render.error_page(request,NO_TICKET,status=404)
    if job["error"]:return render.error_page(request,job["error"])
    if job["finished"] and job["token"] and job["token"] in jobs:return RedirectResponse(url=f"/result/{job['token']}",status_code=303)
    if job["finished"] and not job["token"]:return render.error_page(request,GENERIC_ERROR)
    return templates.TemplateResponse(request=request,name="progress.html",context={"job":job})
@router.get("/upload/state/{ticket}")
def upload_state(ticket:str)->dict:
    job=progress.view(ticket)
    if job is None:return {"found":False,"note":NO_TICKET}
    return {"found":True,**job}
