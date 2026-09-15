from __future__ import annotations
from fastapi import APIRouter,Form,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from starlette.concurrency import run_in_threadpool
from ...core import runtime,update as ota
from ...deps import logger,settings
from .. import render
from ..forms import mode
router=APIRouter()
@router.get("/update",response_class=HTMLResponse)
def update(request:Request):return render.update_page(request)
@router.post("/update/check",response_class=HTMLResponse)
async def update_check(request:Request,branch:str=Form(default="")):
    try:await run_in_threadpool(ota.start,"check",branch.strip() or None)
    except ota.UpdateError as error:return render.update_page(request,error=str(error),status_code=400)
    except Exception:logger.exception("Проверка обновления не запущена");return render.update_page(request,error="Проверка не запущена.",status_code=500)
    return render.update_page(request,message="Проверка запущена.")
@router.post("/update/apply",response_class=HTMLResponse)
async def update_apply(request:Request,branch:str=Form(default="")):
    try:await run_in_threadpool(ota.start,"apply",branch.strip() or None)
    except ota.UpdateError as error:return render.update_page(request,error=str(error),status_code=400)
    except Exception:logger.exception("Обновление не запущено");return render.update_page(request,error="Обновление не запущено.",status_code=500)
    return render.update_page(request,message="Обновление запущено.")
@router.post("/settings/default-mode",response_class=HTMLResponse)
async def save_default_mode(request:Request,verify:str=Form(default="off")):
    try:
        selected=mode(verify)
        await run_in_threadpool(runtime.save_default_verify_mode,settings,selected)
    except ValueError as error:return render.index_page(request,error=str(error),status=400)
    except Exception as error:logger.exception("Режим разбора по умолчанию не сохранён");return render.index_page(request,error=f"Не удалось сохранить режим: {error}",status=500)
    return RedirectResponse(url="/",status_code=303)
@router.get("/update/state")
def update_state()->dict:return ota.status()
