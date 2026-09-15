from __future__ import annotations
from fastapi import APIRouter,File,Form,Request,UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from ...core import runtime
from ...core import vision as vision_core
from ...deps import base,jobs,logger,settings
from ...services.photos import drop_photo,keep_answers,photo_notes,vision_items
from .. import render
from ..forms import is_on,safe_name,whole_number
from ..messages import GENERIC_ERROR,NO_PHOTO,key_not_cleared,settings_not_saved
router=APIRouter()
@router.get("/vision",response_class=HTMLResponse)
def vision(request:Request,token:str=""):return render.vision_page(request,token=token)
@router.post("/vision/settings",response_class=HTMLResponse)
async def vision_settings(request:Request,enabled:str|None=Form(default=None),api_key:str=Form(default=""),model:str=Form(default=""),max_rows:str=Form(default=""),max_photo_mb:str=Form(default=""),timeout:str=Form(default=""),retries:str=Form(default=""),pause_seconds:str=Form(default=""),text_min_score:str=Form(default=""),cache_limit:str=Form(default=""),token:str=Form(default="")):
    fields={"vision_enabled":is_on(enabled),"vision_model":model.strip(),"vision_max_rows":whole_number(max_rows,0),"vision_max_photo_mb":whole_number(max_photo_mb,0),"vision_timeout":whole_number(timeout,0),"vision_retries":whole_number(retries,0),"vision_pause_seconds":pause_seconds.strip(),"vision_text_min_score":text_min_score.strip(),"vision_cache_limit":whole_number(cache_limit,0)}
    if api_key.strip():fields["vision_api_key"]=api_key.strip()
    try:await run_in_threadpool(vision_core.save_config,fields,settings)
    except Exception as error:logger.exception("Настройки распознавания не сохранены");return render.vision_page(request,error=settings_not_saved(error,runtime.runtime_path(settings)),token=token,status_code=500)
    return render.vision_page(request,message="Настройки сохранены.",token=token)
@router.post("/vision/photos",response_class=HTMLResponse)
async def vision_photos(request:Request,token:str=Form(...),photos:list[UploadFile]=File(default=[])):
    job=jobs.alive(token)
    if job is None:return render.vision_page(request,error="Сверка не найдена.",status_code=404)
    current=base(); config=vision_core.load_config(current); limit=int(config["vision_max_photo_mb"])*1024*1024; payload=[]
    for item in photos:
        name=safe_name(item.filename)
        if not vision_core.is_photo(name):continue
        body=await item.read();await item.close()
        if not body or(limit and len(body)>limit):continue
        payload.append((name,body))
    if not payload:return render.vision_page(request,error=NO_PHOTO,token=token,status_code=400)
    try:report=await run_in_threadpool(vision_core.recognize_all,payload,vision_items(job.result,settings),current)
    except Exception:logger.exception("Снимки не разобраны");return render.vision_page(request,error=GENERIC_ERROR,token=token,status_code=500)
    job.photos[:]=list(report.get("results") or []);return render.vision_page(request,message=str(report.get("note") or ""),results=job.photos,token=token)
@router.post("/vision/confirm",response_class=HTMLResponse)
async def vision_confirm(request:Request,token:str=Form(...),photo:str=Form(default=""),digest:str=Form(default=""),text:str=Form(default=""),row:str=Form(default=""),name:str=Form(default=""),source:str=Form(default=""),picked:str=Form(default="")):
    job=jobs.alive(token)
    if job is None:return render.vision_page(request,error="Сверка не найдена.",status_code=404)
    number=whole_number(row,0)
    try:await run_in_threadpool(vision_core.remember,{"photo":photo,"digest":digest,"text":text,"row":number,"name":name,"source":source,"picked":picked},base())
    except Exception:logger.exception("Ответ по снимку не сохранён");return render.vision_page(request,error=GENERIC_ERROR,token=token,status_code=500)
    keep_answers(job,{"rows":[{"row":number}]});drop_photo(job,photo,digest);ok,detail=photo_notes(job,settings)
    return render.vision_page(request,error=detail,token=token,status_code=500) if not ok else render.vision_page(request,message=f"Ответ записан. {detail}".strip(),token=token)
@router.post("/vision/check",response_class=HTMLResponse)
async def vision_check(request:Request,token:str=Form(default="")):
    try:note=await run_in_threadpool(vision_core.check,base())
    except Exception as error:logger.exception("Проверка распознавания не удалась");return render.vision_page(request,error=str(error),token=token,status_code=502)
    return render.vision_page(request,message=str(note),token=token)
@router.post("/vision/cache/clear",response_class=HTMLResponse)
async def vision_cache_clear(request:Request,token:str=Form(default="")):
    try:gone=await run_in_threadpool(vision_core.clear_cache,settings)
    except Exception as error:return render.vision_page(request,error=str(error),token=token,status_code=500)
    return render.vision_page(request,message=f"Записей удалено: {gone}.",token=token)
@router.post("/vision/key/clear",response_class=HTMLResponse)
async def vision_key_clear(request:Request,token:str=Form(default="")):
    try:await run_in_threadpool(vision_core.forget_key,settings)
    except Exception as error:return render.vision_page(request,error=key_not_cleared("Ключ",error,runtime.runtime_path(settings)),token=token,status_code=500)
    return render.vision_page(request,message="Ключ удалён.",token=token)
