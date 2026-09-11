"""Веб-слой: одна страница загрузки и выдача готового файла."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .core.parse import ParseError
from .core.pipeline import PipelineResult, process, work_dir
from .core.repair import RepairError

BASE_DIR = Path(__file__).resolve().parent
settings = Settings.load()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
logger = logging.getLogger("excelkro")

app = FastAPI(title="ExcelKROAutoFormat", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

RESULTS: dict[str, PipelineResult] = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"max_upload_mb": settings.max_upload_mb},
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    folder = work_dir(settings)
    source = folder / (file.filename or "input.xlsx")
    content = await file.read()

    if len(content) > settings.max_upload_mb * 1024 * 1024:
        return _error(request, f"Файл больше {settings.max_upload_mb} МБ.")
    if not str(file.filename or "").lower().endswith(".xlsx"):
        return _error(request, "Нужен файл с расширением .xlsx.")

    source.write_bytes(content)

    try:
        result = process(source, file.filename or source.name, settings)
    except (ParseError, RepairError) as error:
        logger.warning("Ошибка обработки: %s", error)
        return _error(request, str(error))
    except Exception as error:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        return _error(request, f"Не удалось обработать файл: {error}")

    token = result.output_path.parent.name
    RESULTS[token] = result
    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "token": token,
            "summary": result.summary,
            "groups": result.groups,
            "clusters": result.clusters,
            "doubtful": result.doubtful,
            "output_name": result.output_name,
        },
    )


@app.get("/download/{token}")
def download(token: str):
    result = RESULTS.get(token)
    if result is None or not result.output_path.is_file():
        return HTMLResponse("Файл уже удалён. Загрузите сверку заново.", status_code=404)
    return FileResponse(
        path=result.output_path,
        filename=result.output_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/cleanup/{token}")
def cleanup(token: str) -> dict:
    result = RESULTS.pop(token, None)
    if result is not None:
        shutil.rmtree(result.output_path.parent, ignore_errors=True)
    return {"status": "ok"}


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"error": message, "max_upload_mb": settings.max_upload_mb},
        status_code=400,
    )
