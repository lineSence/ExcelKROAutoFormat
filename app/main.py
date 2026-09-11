"""Веб-слой: страница загрузки, подтверждение спорных пар и выдача файла."""

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

app = FastAPI(title="ExcelKROAutoFormat", version="0.2.0")
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

    return _result_page(request, result)


@app.post("/confirm/{token}", response_class=HTMLResponse)
async def confirm(request: Request, token: str):
    """Применяет решения по спорным пересортам и строит файл заново."""
    old = RESULTS.get(token)
    if old is None or old.source_path is None or not old.source_path.is_file():
        return _error(request, "Срок хранения истёк. Загрузите сверку заново.")

    form = await request.form()
    decisions: dict[str, bool] = dict(old.decisions or {})
    for key, value in form.multi_items():
        if not key.startswith("pair-"):
            continue
        answer = str(value).strip()
        if answer == "yes":
            decisions[key[5:]] = True
        elif answer == "no":
            decisions[key[5:]] = False

    try:
        result = process(old.source_path, old.source_name, settings, decisions)
    except (ParseError, RepairError) as error:
        return _error(request, str(error))
    except Exception as error:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        return _error(request, f"Не удалось обработать файл: {error}")

    RESULTS.pop(token, None)
    shutil.rmtree(old.output_path.parent, ignore_errors=True)
    return _result_page(request, result)


def _result_page(request: Request, result: PipelineResult) -> HTMLResponse:
    token = result.output_path.parent.name
    RESULTS[token] = result
    # Подтверждённые пары в таблице не показываются.
    pending = [row for row in result.doubtful if not row.get("answered")]
    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "token": token,
            "summary": result.summary,
            "groups": result.groups,
            "doubtful": pending,
            "confirmed_count": len(result.doubtful) - len(pending),
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
