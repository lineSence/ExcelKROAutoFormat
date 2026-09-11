"""Веб-слой: страница загрузки, подтверждение спорных пар, обучение и выдача файла."""

from __future__ import annotations

import logging
import shutil
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .core import learning, runtime
from .core.meta import SheetMeta
from .core.parse import ParseError
from .core.pipeline import PipelineResult, process, work_dir
from .core.repair import RepairError
from .core.verify import model_status

BASE_DIR = Path(__file__).resolve().parent
settings = Settings.load()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
logger = logging.getLogger("excelkro")

app = FastAPI(title="ExcelKROAutoFormat", version="0.4.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

VERIFY_MODES = ("off", "model")

# Браузер иногда отдаёт пустое тело, если файл перезаписали после выбора
# (ошибка вида «File changed» / ERR_UPLOAD_FILE_CHANGED).
EMPTY_UPLOAD = (
    "Файл не дошёл целиком: похоже, его изменили после выбора. "
    "Закройте его в Excel и выберите заново."
)
EXPIRED = "Срок хранения истёк. Загрузите сверку заново."

RESULTS: dict[str, PipelineResult] = {}
# Режим «только чёткие пересорты» по токену результата: нужен при пересборке.
STRICT_FLAGS: dict[str, bool] = {}
# Режим второго слоя по токену результата.
VERIFY_FLAGS: dict[str, str] = {}


def _mode(value: object) -> str:
    """Режим проверки из формы. Неизвестное значение — чистая логика."""
    text = str(value or "").strip().lower()
    return text if text in VERIFY_MODES else "off"


def _base() -> Settings:
    """Настройки с учётом переключателей из интерфейса."""
    return runtime.apply(settings)


def _settings_for(strict: bool, verify: str = "off") -> Settings:
    """Настройки одного запроса с выбранными режимами."""
    return replace(_base(), strict_resort=bool(strict), verify_mode=_mode(verify))


def _is_on(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "да")


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
            "verify": _mode(settings.verify_mode),
            "model": model_status(settings),
            "embed": runtime.embed_status(_base()),
        },
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    strict: str | None = Form(default=None),
    verify: str | None = Form(default=None),
):
    folder = work_dir(settings)
    source = folder / (file.filename or "input.xlsx")
    content = await file.read()
    strict_on = _is_on(strict)
    verify_mode = _mode(verify)

    if not content:
        return _error(request, EMPTY_UPLOAD, strict_on, verify_mode)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        return _error(request, f"Файл больше {settings.max_upload_mb} МБ.", strict_on, verify_mode)
    if not str(file.filename or "").lower().endswith(".xlsx"):
        return _error(request, "Нужен файл с расширением .xlsx.", strict_on, verify_mode)

    source.write_bytes(content)

    try:
        result = process(
            source,
            file.filename or source.name,
            _settings_for(strict_on, verify_mode),
        )
    except (ParseError, RepairError) as error:
        logger.warning("Ошибка обработки: %s", error)
        return _error(request, str(error), strict_on, verify_mode)
    except Exception as error:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        return _error(request, f"Не удалось обработать файл: {error}", strict_on, verify_mode)

    return _result_page(request, result, strict_on, verify_mode)


def _rebuild(
    request: Request,
    old: PipelineResult,
    decisions: dict[str, bool],
    sheet_meta: SheetMeta,
    strict_on: bool,
    verify_mode: str,
    token: str,
    message: str = "",
) -> HTMLResponse:
    """Собирает файл заново с теми же режимами, решениями и ручными полями."""
    try:
        result = process(
            old.source_path,
            old.source_name,
            _settings_for(strict_on, verify_mode),
            decisions,
            sheet_meta,
        )
    except (ParseError, RepairError) as error:
        return _error(request, str(error), strict_on, verify_mode)
    except Exception as error:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        return _error(request, f"Не удалось обработать файл: {error}", strict_on, verify_mode)

    RESULTS.pop(token, None)
    STRICT_FLAGS.pop(token, None)
    VERIFY_FLAGS.pop(token, None)
    shutil.rmtree(old.output_path.parent, ignore_errors=True)
    return _result_page(request, result, strict_on, verify_mode, message)


@app.post("/meta/{token}", response_class=HTMLResponse)
async def meta(request: Request, token: str):
    """Вносит в сверку ручные поля: причину, дату, продавцов и подписи."""
    old = RESULTS.get(token)
    if old is None or old.source_path is None or not old.source_path.is_file():
        return _error(request, EXPIRED)

    form = await request.form()
    sheet_meta = SheetMeta.from_form({key: form.get(key) for key in form.keys()})
    strict_on = _is_on(form.get("strict")) or STRICT_FLAGS.get(token, False)
    verify_mode = _mode(form.get("verify") or VERIFY_FLAGS.get(token, "off"))

    return _rebuild(
        request,
        old,
        dict(old.decisions or {}),
        sheet_meta,
        strict_on,
        verify_mode,
        token,
        message="Данные сверки внесены в файл.",
    )


@app.post("/confirm/{token}", response_class=HTMLResponse)
async def confirm(request: Request, token: str):
    """Применяет решения по спорным пересортам и строит файл заново."""
    old = RESULTS.get(token)
    if old is None or old.source_path is None or not old.source_path.is_file():
        return _error(request, EXPIRED)

    form = await request.form()
    decisions: dict[str, bool] = dict(old.decisions or {})
    answers: dict[str, bool] = {}
    for key, value in form.multi_items():
        if not key.startswith("pair-"):
            continue
        answer = str(value).strip()
        if answer == "yes":
            decisions[key[5:]] = True
            answers[key[5:]] = True
        elif answer == "no":
            decisions[key[5:]] = False
            answers[key[5:]] = False

    # Режимы сохраняются с первого прогона.
    strict_on = _is_on(form.get("strict")) or STRICT_FLAGS.get(token, False)
    verify_mode = _mode(form.get("verify") or VERIFY_FLAGS.get(token, "off"))

    # Ответы человека — готовые примеры для обучения.
    if answers:
        try:
            samples = learning.samples_from_decisions(
                old.doubtful,
                answers,
                source=old.source_name or "ручное подтверждение",
            )
            learning.append_samples(
                settings.train_store_path,
                samples,
                settings.train_max_samples,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось сохранить примеры обучения")

    # Ручные поля сверки не теряются при пересборке.
    return _rebuild(
        request,
        old,
        decisions,
        old.sheet_meta,
        strict_on,
        verify_mode,
        token,
    )


def _result_page(
    request: Request,
    result: PipelineResult,
    strict: bool = False,
    verify: str = "off",
    message: str = "",
) -> HTMLResponse:
    token = result.output_path.parent.name
    RESULTS[token] = result
    STRICT_FLAGS[token] = bool(strict)
    VERIFY_FLAGS[token] = _mode(verify)
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
            "verify": _mode(verify),
            "meta": result.sheet_meta.to_form(),
            "message": message,
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
    STRICT_FLAGS.pop(token, None)
    VERIFY_FLAGS.pop(token, None)
    if result is not None:
        shutil.rmtree(result.output_path.parent, ignore_errors=True)
    return {"status": "ok"}


# --- Режим обучения -------------------------------------------------------


def _training_page(
    request: Request,
    message: str = "",
    error: str = "",
    report: dict | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="training.html",
        context={
            "model": model_status(settings),
            "embed": runtime.embed_status(_base()),
            "stats": learning.dataset_stats(settings.train_store_path),
            "message": message,
            "error": error,
            "report": report,
            "max_upload_mb": settings.max_upload_mb,
        },
        status_code=status_code,
    )


@app.get("/training", response_class=HTMLResponse)
def training(request: Request):
    return _training_page(request)


@app.post("/training/samples", response_class=HTMLResponse)
async def training_samples(
    request: Request,
    file: UploadFile = File(...),
):
    """Забирает примеры из ручной сверки: зелёные пары — да, остальные — нет."""
    content = await file.read()
    name = str(file.filename or "образец.xlsx")

    if not content:
        return _training_page(request, error=EMPTY_UPLOAD, status_code=400)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        return _training_page(request, error=f"Файл больше {settings.max_upload_mb} МБ.", status_code=400)
    if not name.lower().endswith(".xlsx"):
        return _training_page(request, error="Нужен файл с расширением .xlsx.", status_code=400)

    folder = work_dir(settings)
    source = folder / name
    source.write_bytes(content)
    try:
        samples = learning.samples_from_manual(
            source,
            tuple(settings.type_words),
            source=name,
        )
        stored = learning.append_samples(
            settings.train_store_path,
            samples,
            settings.train_max_samples,
        )
    except Exception as error:  # noqa: BLE001
        logger.exception("Не удалось разобрать образец")
        return _training_page(request, error=f"Не удалось разобрать образец: {error}", status_code=400)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    positives = sum(1 for sample in samples if sample.label)
    return _training_page(
        request,
        message=(
            f"Из файла «{name}» добавлено {stored['added']} примеров "
            f"({positives} подтверждённых пересортов), повторов пропущено "
            f"{stored['skipped']}. Всего в базе: {stored['total']}."
        ),
    )


@app.post("/training/train", response_class=HTMLResponse)
def training_train(request: Request):
    """Переобучает модель на всех накопленных примерах."""
    try:
        report = learning.train_model(settings)
    except ValueError as error:
        return _training_page(request, error=str(error), status_code=400)
    except Exception as error:  # noqa: BLE001
        logger.exception("Обучение не удалось")
        return _training_page(request, error=f"Обучение не удалось: {error}", status_code=400)
    return _training_page(request, message="Модель переобучена и сохранена.", report=report)


@app.post("/training/embed")
def training_embed(embed: str | None = Form(default=None)):
    """Включает или выключает эмбеддинги имён без перезапуска службы."""
    runtime.set_embed(settings, _is_on(embed))
    return RedirectResponse(url="/training", status_code=303)


@app.post("/training/clear")
def training_clear():
    """Очищает накопленные примеры. Модель остаётся прежней."""
    learning.clear_samples(settings.train_store_path)
    return RedirectResponse(url="/training", status_code=303)


def _error(
    request: Request,
    message: str,
    strict: bool = False,
    verify: str = "off",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": message,
            "max_upload_mb": settings.max_upload_mb,
            "strict": bool(strict),
            "verify": _mode(verify),
            "model": model_status(settings),
            "embed": runtime.embed_status(_base()),
        },
        status_code=400,
    )
