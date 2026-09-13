"""Веб-слой: загрузка, подтверждение спорных пар, выдача файла, справочники."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import Settings
from .core import refs_sync
from .core.guard import UploadTooLarge
from .core.parse import ParseError
from .core.pipeline import PipelineResult, process, sweep, work_dir
from .core.repair import RepairError

BASE_DIR = Path(__file__).resolve().parent
settings = Settings.load()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
logger = logging.getLogger("excelkro")

app = FastAPI(title="ExcelKROAutoFormat", version=__version__)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Размер части при записи загружаемого файла на диск.
CHUNK = 1024 * 1024
UNSAFE_NAME = re.compile(r"[\\/\x00]+")

RESULTS: dict[str, PipelineResult] = {}
# Режим «только чёткие пересорты» по токену результата: нужен при пересборке.
STRICT_FLAGS: dict[str, bool] = {}

GENERIC_ERROR = "Не удалось обработать файл. Подробности — в журнале службы."


def _settings_for(strict: bool) -> Settings:
    """Настройки одного запроса с выбранным режимом подбора пар."""
    return replace(settings, strict_resort=bool(strict))


def _is_on(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _safe_name(name: object) -> str:
    """Имя файла без пути: клиент может прислать «../../secret.xlsx»."""
    base = Path(str(name or "")).name
    base = UNSAFE_NAME.sub("", base).strip().strip(".")
    return base or "input.xlsx"


def _forget(token: str) -> None:
    """Убирает результат из памяти и удаляет его рабочую папку."""
    result = RESULTS.pop(token, None)
    STRICT_FLAGS.pop(token, None)
    if result is not None:
        shutil.rmtree(result.output_path.parent, ignore_errors=True)


def _drop_expired() -> None:
    """Чистит диск и память от работ со истёкшим сроком хранения."""
    sweep(settings)
    for token in list(RESULTS):
        if not RESULTS[token].output_path.is_file():
            RESULTS.pop(token, None)
            STRICT_FLAGS.pop(token, None)


async def _save_upload(file: UploadFile, target: Path, limit_mb: int) -> int:
    """Пишет загрузку на диск по частям и обрывает её при превышении предела."""
    limit = limit_mb * 1024 * 1024
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as sink:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadTooLarge
                sink.write(chunk)
    except UploadTooLarge:
        target.unlink(missing_ok=True)
        raise
    return size


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "max_upload_mb": settings.max_upload_mb,
            "strict": settings.strict_resort,
        },
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    strict: str | None = Form(default=None),
):
    _drop_expired()
    strict_on = _is_on(strict)
    name = _safe_name(file.filename)

    if not name.lower().endswith(".xlsx"):
        return _error(request, "Нужен файл с расширением .xlsx.", strict_on)

    folder = work_dir(settings)
    source = folder / name
    try:
        await _save_upload(file, source, settings.max_upload_mb)
    except UploadTooLarge:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, f"Файл больше {settings.max_upload_mb} МБ.", strict_on)

    try:
        result = process(source, name, _settings_for(strict_on), folder=folder)
    except (ParseError, RepairError) as error:
        logger.warning("Ошибка обработки: %s", error)
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, GENERIC_ERROR, strict_on, status=500)

    return _result_page(request, result, strict_on)


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

    # Режим подбора сохраняется с первого прогона.
    strict_on = _is_on(form.get("strict")) or STRICT_FLAGS.get(token, False)
    folder = work_dir(settings)
    # Исходный файл переносится в новую папку: старая будет удалена.
    source = folder / old.source_path.name
    shutil.copyfile(old.source_path, source)

    try:
        result = process(
            source,
            old.source_name,
            _settings_for(strict_on),
            decisions,
            folder=folder,
        )
    except (ParseError, RepairError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, GENERIC_ERROR, strict_on, status=500)

    _forget(token)
    return _result_page(request, result, strict_on)


def _note_refs(result: PipelineResult) -> None:
    """Кладёт итог справочников в состояние, чтобы он был виден на `/refs`."""
    info = result.refs or {}
    try:
        refs_sync.note_fill(
            settings.refs_state_path,
            store=str(result.summary.get("warehouse") or ""),
            day=str(result.summary.get("date") or ""),
            file_name=result.source_name,
            found=bool(info.get("found")),
            problems=list(info.get("problems") or []),
        )
    except OSError:
        # Запись журнала не должна мешать выдаче готового файла.
        logger.warning("Итог справочников не записан в состояние", exc_info=True)


def _result_page(
    request: Request,
    result: PipelineResult,
    strict: bool = False,
) -> HTMLResponse:
    token = result.output_path.parent.name
    RESULTS[token] = result
    STRICT_FLAGS[token] = bool(strict)
    _note_refs(result)
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
            "strict": bool(strict),
            "refs": result.refs,
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
    _forget(token)
    return {"status": "ok"}


# --- Справочники ------------------------------------------------------------


def _refs_page(request: Request, note: str = "", error: str = "") -> HTMLResponse:
    """Страница справочников. Книги здесь не разбираются: только состояние."""
    state = refs_sync.load_state(settings.refs_state_path)
    return templates.TemplateResponse(
        request=request,
        name="refs.html",
        context={
            "state": state,
            "books": refs_sync.book_status(settings.refs_dir),
            "checker": settings.default_checker,
            "cells": settings.refs_cells(),
            "local_dir": settings.refs_dir,
            "max_upload_mb": settings.max_upload_mb,
            "fill_log": state.fill_log,
            "min_score": settings.refs_match_min_score,
            "days_around": settings.refs_days_around,
            "note": note,
            "error": error,
        },
    )


@app.get("/refs", response_class=HTMLResponse)
def refs_page(request: Request):
    return _refs_page(request)


@app.post("/refs/upload", response_class=HTMLResponse)
async def refs_upload(
    request: Request,
    planning: UploadFile | None = File(default=None),
    schedule: UploadFile | None = File(default=None),
):
    """Ручная загрузка книг справочников. Книги только сохраняются."""
    state = refs_sync.load_state(settings.refs_state_path)
    saved: list[str] = []

    for kind, sent in (("planning", planning), ("schedule", schedule)):
        if sent is None or not (sent.filename or "").strip():
            continue
        name = _safe_name(sent.filename)
        if not name.lower().endswith(".xlsx"):
            return _refs_page(request, error=f"Нужен файл .xlsx, получен: {name}")
        target = refs_sync.book_target(settings.refs_dir, kind)
        try:
            await _save_upload(sent, target, settings.max_upload_mb)
        except UploadTooLarge:
            return _refs_page(
                request,
                error=f"Файл {name} больше {settings.max_upload_mb} МБ.",
            )
        except OSError:
            logger.exception("Не удалось сохранить справочник")
            return _refs_page(request, error="Файл не сохранён. Подробности — в журнале службы.")
        state = refs_sync.mark_upload(state, kind, name, settings.refs_state_path)
        saved.append(name)

    if not saved:
        return _refs_page(request, error="Файлы не выбраны.")
    return _refs_page(
        request,
        note="Загружено: " + ", ".join(saved) + ". Книги разберутся при первой сверке.",
    )


@app.post("/refs/check", response_class=HTMLResponse)
def refs_check(request: Request):
    """Разбирает загруженные книги по кнопке и показывает итог."""
    state = refs_sync.load_state(settings.refs_state_path)
    state = refs_sync.measure(state, settings.refs_dir, settings.refs_state_path)
    if state.last_status == "ошибка":
        return _refs_page(request, error=f"Книги не разобраны. {state.last_error}")
    return _refs_page(
        request,
        note=f"Разбор готов: складов {state.stores}, фамилий {state.people}.",
    )


@app.post("/refs/clear", response_class=HTMLResponse)
def refs_clear(request: Request):
    """Очищает блок ошибок справочников."""
    refs_sync.clear_fill_log(settings.refs_state_path)
    return _refs_page(request, note="Блок ошибок очищен.")


def _error(
    request: Request,
    message: str,
    strict: bool = False,
    status: int = 400,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": message,
            "max_upload_mb": settings.max_upload_mb,
            "strict": bool(strict),
        },
        status_code=status,
    )
