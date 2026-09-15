from __future__ import annotations
from fastapi import APIRouter,Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from ...core import update as ota
from ...deps import logger
from .. import render
router=APIRouter()
@router.get("/update",response_class=HTMLResponse)
def update(request:Request):return render.update_page(request)
@router.post("/update/check",response_class=HTMLResponse)
async def update_check(request:Request):
    try:await run_in_threadpool(ota.start,"check")
    except ota.UpdateError as error:return render.update_page(request,error=str(error),status_code=400)
    except Exception:logger.exception("Проверка обновления не запущена");return render.update_page(request,error="Проверка не запущена.",status_code=500)
    return render.update_page(request,message="Проверка запущена.")
@router.post("/update/apply",response_class=HTMLResponse)
async def update_apply(request:Request):
    try:await run_in_threadpool(ota.start,"apply")
    except ota.UpdateError as error:return render.update_page(request,error=str(error),status_code=400)
    except Exception:logger.exception("Обновление не запущено");return render.update_page(request,error="Обновление не запущено.",status_code=500)
    return render.update_page(request,message="Обновление запущено.")
@router.get("/update/state")
def update_state()->dict:return ota.status()
