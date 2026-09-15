from __future__ import annotations
from fastapi import APIRouter,Request
from fastapi.responses import HTMLResponse
from ... import __version__
from .. import render
router=APIRouter()
@router.get("/health")
def health()->dict:return {"status":"ok","version":__version__}
@router.get("/",response_class=HTMLResponse)
def index(request:Request):return render.index_page(request)
