"""Веб-слой: загрузка, спорные пары, ручные поля, справочники, обучение.

Тяжёлая работа (ремонт, разбор, справочники, обучение) выполняется в
отдельном потоке через `run_in_threadpool`. Внутри `async def` обработчика
нельзя вызывать блокирующий код напрямую: на время разбора встаёт весь
сервер, и даже /health не отвечает.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import Settings
from .core import claims as claims_book
from .core import learning, refs_sync, runtime
from .core import update as ota
from .core.guard import UploadTooLarge
from .core.meta import SheetMeta
from .core.parse import ParseError
from .core.pipeline import PipelineResult, process, sweep, work_dir
from .core.repair import RepairError
from .core.verify import model_status

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

VERIFY_MODES = ("off", "model")
# Поля формы сверки, у которых может быть несколько значений.
META_MULTI_FIELDS = ("sellers", "seller_hours", "auditors")
# Префикс полей формы с заявками реестра расхождений.
CLAIM_PREFIX = "claim-"

RESULTS: dict[str, PipelineResult] = {}
# Режим «только чёткие пересорты» по токену результата: нужен при пересборке.
STRICT_FLAGS: dict[str, bool] = {}
# Режим второго слоя по токену результата.
VERIFY_FLAGS: dict[str, str] = {}

GENERIC_ERROR = "Не удалось обработать файл. Подробности — в журнале службы."
EMPTY_UPLOAD = (
    "Файл не дошёл целиком: похоже, его изменили после выбора. "
    "Закройте его в Excel и выберите заново."
)
EXPIRED = "Срок хранения истёк. Загрузите сверку заново."


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


def _safe_name(name: object) -> str:
    """Имя файла без пути: клиент может прислать «../../secret.xlsx»."""
    base = Path(str(name or "")).name
    base = UNSAFE_NAME.sub("", base).strip().strip(".")
    return base or "input.xlsx"


def _forget(token: str) -> None:
    """Убирает результат из памяти и удаляет его рабочую папку."""
    result = RESULTS.pop(token, None)
    STRICT_FLAGS.pop(token, None)
    VERIFY_FLAGS.pop(token, None)
    if result is not None:
        shutil.rmtree(result.output_path.parent, ignore_errors=True)


def _drop_expired() -> None:
    """Чистит диск и память от работ со истёкшим сроком хранения."""
    sweep(settings)
    for token in list(RESULTS):
        if not RESULTS[token].output_path.is_file():
            RESULTS.pop(token, None)
            STRICT_FLAGS.pop(token, None)
            VERIFY_FLAGS.pop(token, None)


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


async def _run_pipeline(
    source: Path,
    original_name: str,
    strict: bool,
    verify: str,
    folder: Path,
    decisions: dict[str, bool] | None = None,
    sheet_meta: SheetMeta | None = None,
    prev_path: Path | None = None,
    prev_name: str = "",
    claim_decisions: dict[str, bool] | None = None,
) -> PipelineResult:
    """Запускает обработку в отдельном потоке: сервер остаётся отзывчивым."""
    return await run_in_threadpool(
        process,
        source,
        original_name,
        _settings_for(strict, verify),
        decisions,
        folder,
        sheet_meta,
        prev_path,
        prev_name,
        claim_decisions,
    )


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
    prev: UploadFile | None = File(default=None),
    strict: str | None = Form(default=None),
    verify: str | None = Form(default=None),
):
    await run_in_threadpool(_drop_expired)
    strict_on = _is_on(strict)
    verify_mode = _mode(verify)
    name = _safe_name(file.filename)

    if not name.lower().endswith(".xlsx"):
        return _error(request, "Нужен файл с расширением .xlsx.", strict_on, verify_mode)

    folder = work_dir(settings)
    source = folder / name
    try:
        size = await _save_upload(file, source, settings.max_upload_mb)
    except UploadTooLarge:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(
            request,
            f"Файл больше {settings.max_upload_mb} МБ.",
            strict_on,
            verify_mode,
        )

    if not size:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, EMPTY_UPLOAD, strict_on, verify_mode)

    # Второй файл — предыдущая инвентаризация, он необязательный.
    prev_path: Path | None = None
    prev_name = ""
    if prev is not None and (prev.filename or "").strip():
        prev_name = _safe_name(prev.filename)
        if not prev_name.lower().endswith(".xlsx"):
            shutil.rmtree(folder, ignore_errors=True)
            return _error(
                request,
                "Предыдущая сверка должна быть файлом .xlsx.",
                strict_on,
                verify_mode,
            )
        prev_path = folder / f"prev-{prev_name}"
        try:
            prev_size = await _save_upload(prev, prev_path, settings.max_upload_mb)
        except UploadTooLarge:
            shutil.rmtree(folder, ignore_errors=True)
            return _error(
                request,
                f"Предыдущая сверка больше {settings.max_upload_mb} МБ.",
                strict_on,
                verify_mode,
            )
        if not prev_size:
            prev_path, prev_name = None, ""

    try:
        result = await _run_pipeline(
            source,
            name,
            strict_on,
            verify_mode,
            folder,
            None,
            None,
            prev_path,
            prev_name,
            None,
        )
    except (ParseError, RepairError) as error:
        logger.warning("Ошибка обработки: %s", error)
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on, verify_mode)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, GENERIC_ERROR, strict_on, verify_mode, status=500)

    return _result_page(request, result, strict_on, verify_mode)


async def _rebuild(
    request: Request,
    old: PipelineResult,
    decisions: dict[str, bool],
    sheet_meta: SheetMeta | None,
    strict_on: bool,
    verify_mode: str,
    token: str,
    message: str = "",
    claim_decisions: dict[str, bool] | None = None,
) -> HTMLResponse:
    """Собирает файл заново с теми же режимами, решениями и ручными полями."""
    folder = work_dir(settings)
    # Исходный файл переносится в новую папку: старая будет удалена.
    source = folder / old.source_path.name
    await run_in_threadpool(shutil.copyfile, old.source_path, source)

    # Предыдущая сверка тоже не теряется при пересборке.
    prev_path: Path | None = None
    if old.prev_path is not None and old.prev_path.is_file():
        prev_path = folder / old.prev_path.name
        await run_in_threadpool(shutil.copyfile, old.prev_path, prev_path)

    if claim_decisions is None:
        claim_decisions = dict(old.claim_decisions or {})

    try:
        result = await _run_pipeline(
            source,
            old.source_name,
            strict_on,
            verify_mode,
            folder,
            decisions,
            sheet_meta,
            prev_path,
            old.prev_name,
            claim_decisions,
        )
    except (ParseError, RepairError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on, verify_mode)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, GENERIC_ERROR, strict_on, verify_mode, status=500)

    _forget(token)
    return _result_page(request, result, strict_on, verify_mode, message)


@app.post("/meta/{token}", response_class=HTMLResponse)
async def meta(request: Request, token: str):
    """Вносит в сверку ручные поля: причину, дату, продавцов и подписи."""
    old = RESULTS.get(token)
    if old is None or old.source_path is None or not old.source_path.is_file():
        return _error(request, EXPIRED)

    form = await request.form()
    # Продавцы, часы и ревизоры приходят повторяющимися полями.
    payload = {
        key: (form.getlist(key) if key in META_MULTI_FIELDS else form.get(key))
        for key in form.keys()
    }
    sheet_meta = SheetMeta.from_form(payload)
    strict_on = _is_on(form.get("strict")) or STRICT_FLAGS.get(token, False)
    verify_mode = _mode(form.get("verify") or VERIFY_FLAGS.get(token, "off"))

    return await _rebuild(
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
            await run_in_threadpool(_store_answers, old, answers)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось сохранить примеры обучения")

    # Ручные поля сверки не теряются при пересборке.
    return await _rebuild(
        request,
        old,
        decisions,
        old.sheet_meta,
        strict_on,
        verify_mode,
        token,
    )


@app.post("/claims/{token}", response_class=HTMLResponse)
async def claims_confirm(request: Request, token: str):
    """Вносит подтверждённые заявки реестра расхождений в файл.

    Подтверждение идёт по строкам: отмеченные галочкой заявки получают
    пометку и выходят из подбора пересортов, остальные остаются как были.
    """
    old = RESULTS.get(token)
    if old is None or old.source_path is None or not old.source_path.is_file():
        return _error(request, EXPIRED)

    form = await request.form()
    claim_decisions: dict[str, bool] = dict(old.claim_decisions or {})
    for key, value in form.multi_items():
        if not key.startswith(CLAIM_PREFIX):
            continue
        claim_decisions[key[len(CLAIM_PREFIX):]] = _is_on(value)

    strict_on = _is_on(form.get("strict")) or STRICT_FLAGS.get(token, False)
    verify_mode = _mode(form.get("verify") or VERIFY_FLAGS.get(token, "off"))
    chosen = sum(1 for value in claim_decisions.values() if value)

    return await _rebuild(
        request,
        old,
        dict(old.decisions or {}),
        old.sheet_meta,
        strict_on,
        verify_mode,
        token,
        message=f"Заявки реестра внесены в файл: {chosen}.",
        claim_decisions=claim_decisions,
    )


def _store_answers(old: PipelineResult, answers: dict[str, bool]) -> None:
    """Кладёт ответы по спорным парам в базу примеров."""
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
    except Exception:  # noqa: BLE001
        # Запись журнала не должна мешать выдаче готового файла.
        logger.warning("Итог справочников не записан в состояние", exc_info=True)


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
            "clusters": result.clusters,
            "doubtful": pending,
            "confirmed_count": len(result.doubtful) - len(pending),
            "output_name": result.output_name,
            "strict": bool(strict),
            "verify": _mode(verify),
            "refs": result.refs,
            "meta": result.sheet_meta.to_form(),
            "comparison": result.comparison,
            "claims": result.claims,
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
            "claims": claims_book.status(settings.refs_dir),
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
    claims: UploadFile | None = File(default=None),
):
    """Ручная загрузка книг справочников. Книги только сохраняются."""
    state = refs_sync.load_state(settings.refs_state_path)
    saved: list[str] = []

    sent_books = (
        ("planning", planning),
        ("schedule", schedule),
        ("claims", claims),
    )
    for kind, sent in sent_books:
        if sent is None or not (sent.filename or "").strip():
            continue
        name = _safe_name(sent.filename)
        if not name.lower().endswith(".xlsx"):
            return _refs_page(request, error=f"Нужен файл .xlsx, получен: {name}")
        if kind == "claims":
            target = claims_book.book_path(settings.refs_dir)
        else:
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
        if kind == "claims":
            # Кеш реестра перестроится сам при первой сверке.
            claims_book.forget_cache(settings.refs_dir)
        else:
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
    """Разбирает загруженные книги по кнопке и показывает итог.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток, поэтому
    долгий разбор книг не блокирует остальные страницы.
    """
    state = refs_sync.load_state(settings.refs_state_path)
    state = refs_sync.measure(state, settings.refs_dir, settings.refs_state_path)
    claims_info = claims_book.status(settings.refs_dir)
    if state.last_status == "ошибка":
        return _refs_page(request, error=f"Книги не разобраны. {state.last_error}")
    tail = f", записей реестра {claims_info['rows']}" if claims_info.get("found") else ""
    return _refs_page(
        request,
        note=f"Разбор готов: складов {state.stores}, фамилий {state.people}{tail}.",
    )


@app.post("/refs/clear", response_class=HTMLResponse)
def refs_clear(request: Request):
    """Очищает блок ошибок справочников."""
    refs_sync.clear_fill_log(settings.refs_state_path)
    return _refs_page(request, note="Блок ошибок очищен.")


# --- Обновление программы (OTA) ---------------------------------------------


def _update_page(
    request: Request,
    message: str = "",
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Страница обновления: текущая версия, кнопки и состояние последнего запуска."""
    info = ota.status()
    return templates.TemplateResponse(
        request=request,
        name="update.html",
        context={
            "status": info,
            "state": info["state"],
            "message": message,
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/update", response_class=HTMLResponse)
def update_page(request: Request):
    return _update_page(request)


@app.post("/update/check", response_class=HTMLResponse)
def update_check(request: Request):
    """Смотрит, есть ли в Git версия новее установленной. Файлы не меняются."""
    try:
        ota.start("check")
    except ota.UpdateError as error:
        return _update_page(request, error=str(error), status_code=400)
    return _update_page(request, message="Проверка запущена. Итог появится в блоке состояния.")


@app.post("/update/apply", response_class=HTMLResponse)
def update_apply(request: Request):
    """Ставит последнюю версию и перезапускает службу.

    Обновление идёт в отдельной службе systemd, поэтому перезапуск этого же
    веб-слоя не обрывает работу на полпути. Страница отвечает сразу, а ход
    работы виден в блоке состояния.
    """
    try:
        ota.start("apply")
    except ota.UpdateError as error:
        return _update_page(request, error=str(error), status_code=400)
    return _update_page(
        request,
        message=(
            "Обновление запущено. Служба перезапустится сама: если страница "
            "ненадолго перестанет отвечать, обновите её через полминуты."
        ),
    )


@app.get("/update/state")
def update_state() -> dict:
    """Состояние обновления в виде JSON: удобно для проверок извне."""
    return ota.status()


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
    name = _safe_name(file.filename)
    if not name.lower().endswith(".xlsx"):
        return _training_page(request, error="Нужен файл с расширением .xlsx.", status_code=400)

    folder = work_dir(settings)
    source = folder / name
    try:
        size = await _save_upload(file, source, settings.max_upload_mb)
    except UploadTooLarge:
        shutil.rmtree(folder, ignore_errors=True)
        return _training_page(
            request,
            error=f"Файл больше {settings.max_upload_mb} МБ.",
            status_code=400,
        )

    if not size:
        shutil.rmtree(folder, ignore_errors=True)
        return _training_page(request, error=EMPTY_UPLOAD, status_code=400)

    try:
        samples, stored = await run_in_threadpool(_read_samples, source, name)
    except Exception as error:  # noqa: BLE001
        logger.exception("Не удалось разобрать образец")
        return _training_page(
            request,
            error=f"Не удалось разобрать образец: {error}",
            status_code=400,
        )
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


def _read_samples(source: Path, name: str) -> tuple[list, dict]:
    """Разбор образца ручной сверки и запись примеров в базу."""
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
    return samples, stored


@app.post("/training/train", response_class=HTMLResponse)
def training_train(request: Request):
    """Переобучает модель на всех накопленных примерах.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток, и
    долгое обучение не блокирует остальные страницы.
    """
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
    status: int = 400,
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
        status_code=status,
    )
