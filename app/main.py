"""Веб-слой: загрузка, спорные пары, ручные поля, справочники, обучение.

Тяжёлая работа (ремонт, разбор, справочники, обучение, распознавание
фото) выполняется в отдельном потоке через `run_in_threadpool`. Внутри
`async def` обработчика нельзя вызывать блокирующий код напрямую: на время
разбора встаёт весь сервер, и даже /health не отвечает.

Разобранная сверка живёт в памяти по токену, поэтому у неё есть свой
постоянный адрес `/result/{токен}`. Уход на другую страницу и возврат
ничего не стирают: работа продолжается с того же места.

У сверки два переключателя второго слоя: сам режим (`verify`: логика,
локальная модель или LLM внешнего провайдера) и влияние детерминированной
логики (`logic`, 0…100%). Оба запоминаются по токену, чтобы пересборка
шла в тех же условиях.

Письма ревизоров связаны со сверкой напрямую: сразу после обработки
сверки снимки из писем уходят в распознавание (`mail_vision`), ответы
модели попадают на страницу «Фото товара» по тому же токену, а кнопка
архива письма рядом со скачиванием файла отдаёт уже обработанный архив со
снимками по имени узнанного товара. Подсказки из текста (товар в списке
несосчитанного, ФИО ночного продавца) по-прежнему попадают в форму данных
сверки.

Список писем не теряется при перезапуске службы: он читается с диска
(`mail_store`, файл `mail-letters.json`). Без этого кнопки архивов
пропадали после каждого перезапуска, а вернуть их было нечем — номера
разобранных писем лежат в `mail-state.json`, и повторный заход в ящик
отвечал «новых писем нет».
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
from .core import embed as embed_core
from .core import judge as judge_core
from .core import learning, mail_store, mail_vision, photo_mark, refs_sync, runtime
from .core import mail as mail_core
from .core import update as ota
from .core import vision as vision_core
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

# off — только логика, model — локальная модель, llm — языковая модель.
VERIFY_MODES = ("off", "model", "llm")
# Влияние логики по умолчанию: решает детерминированный расчёт.
DEFAULT_LOGIC = 100
# Поля формы сверки, у которых может быть несколько значений.
META_MULTI_FIELDS = ("sellers", "seller_hours", "auditors")
# Префикс полей формы с заявками реестра расхождений.
CLAIM_PREFIX = "claim-"

RESULTS: dict[str, PipelineResult] = {}
# Режим «только чёткие пересорты» по токену результата: нужен при пересборке.
STRICT_FLAGS: dict[str, bool] = {}
# Режим второго слоя по токену результата.
VERIFY_FLAGS: dict[str, str] = {}
# Влияние логики (проценты) по токену результата.
LOGIC_FLAGS: dict[str, int] = {}
# Разобранные снимки по токену результата: подтверждение одного снимка
# не должно стирать остальные ответы модели.
PHOTOS: dict[str, list] = {}
# Строки с подтверждённым фото по токену результата: по ним снимается
# пометка «нет фото» после каждой сборки файла.
PHOTO_ROWS: dict[str, set[int]] = {}
# Отчёт о разборе снимков из писем по токену сверки: разбор идёт один раз
# при обработке, а не при каждом скачивании архива письма.
MAIL_VISION: dict[str, dict] = {}
# Разобранные письма. Ящик один на службу, поэтому токена сверки здесь нет:
# список общий. Он читается с диска при старте и пишется после каждого
# захода в ящик, иначе кнопки архивов пропадали бы после перезапуска.
MAIL_LETTERS: list = []
MAIL_SKIPPED: list[str] = []

try:
    MAIL_LETTERS[:] = mail_store.load(settings)
except Exception:  # noqa: BLE001
    logger.warning("Письма прошлых заходов не восстановлены", exc_info=True)

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


def _percent(value: object, default: int = DEFAULT_LOGIC) -> int:
    """Влияние логики из формы: целое число от 0 до 100."""
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not text:
        return default
    try:
        number = int(round(float(text)))
    except ValueError:
        return default
    return min(100, max(0, number))


def _base() -> Settings:
    """Настройки с учётом переключателей из интерфейса."""
    return runtime.apply(settings)


def _settings_for(strict: bool, verify: str = "off", logic: int = DEFAULT_LOGIC) -> Settings:
    """Настройки одного запроса с выбранными режимами."""
    return replace(
        _base(),
        strict_resort=bool(strict),
        verify_mode=_mode(verify),
        logic_weight=_percent(logic) / 100.0,
    )


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
    LOGIC_FLAGS.pop(token, None)
    PHOTOS.pop(token, None)
    PHOTO_ROWS.pop(token, None)
    MAIL_VISION.pop(token, None)
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
            LOGIC_FLAGS.pop(token, None)
            PHOTOS.pop(token, None)
            PHOTO_ROWS.pop(token, None)
            MAIL_VISION.pop(token, None)


def _jobs(with_surplus: bool = False) -> list[dict]:
    """Сверки, которые ещё живут в памяти.

    Этот список показывается на главной странице и на странице фото: по нему
    человек возвращается к своей работе после перехода в другой раздел.
    """
    jobs: list[dict] = []
    for key, result in RESULTS.items():
        if not result.output_path.is_file():
            continue
        job = {
            "token": key,
            "warehouse": str(result.summary.get("warehouse") or "сверка"),
            "date": str(result.summary.get("date") or "без даты"),
            "file": result.output_name,
            "pending": sum(1 for row in result.doubtful if not row.get("answered")),
            "photos": len(PHOTOS.get(key, [])),
        }
        if with_surplus:
            job["surplus"] = len(vision_core.surplus_items(_vision_items(result)))
        jobs.append(job)
    return jobs


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
    logic: int = DEFAULT_LOGIC,
) -> PipelineResult:
    """Запускает обработку в отдельном потоке: сервер остаётся отзывчивым."""
    return await run_in_threadpool(
        process,
        source,
        original_name,
        _settings_for(strict, verify, logic),
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
    base = _base()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "max_upload_mb": settings.max_upload_mb,
            "strict": settings.strict_resort,
            "verify": _mode(settings.verify_mode),
            "logic": _percent(round(float(getattr(base, "logic_weight", 1.0)) * 100)),
            "model": model_status(settings),
            "embed": runtime.embed_status(base),
            "judge": runtime.judge_status(base),
            "jobs": _jobs(),
        },
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    prev: UploadFile | None = File(default=None),
    strict: str | None = Form(default=None),
    verify: str | None = Form(default=None),
    logic: str | None = Form(default=None),
):
    await run_in_threadpool(_drop_expired)
    strict_on = _is_on(strict)
    verify_mode = _mode(verify)
    logic_percent = _percent(logic)
    name = _safe_name(file.filename)

    if not name.lower().endswith(".xlsx"):
        return _error(
            request, "Нужен файл с расширением .xlsx.", strict_on, verify_mode, logic_percent
        )

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
            logic_percent,
        )

    if not size:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, EMPTY_UPLOAD, strict_on, verify_mode, logic_percent)

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
                logic_percent,
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
                logic_percent,
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
            logic_percent,
        )
    except (ParseError, RepairError) as error:
        logger.warning("Ошибка обработки: %s", error)
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on, verify_mode, logic_percent)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(
            request, GENERIC_ERROR, strict_on, verify_mode, logic_percent, status=500
        )

    token = result.output_path.parent.name
    # Плюсующие строки сразу получают пометку «нет фото»: фото по ним ещё нет.
    await run_in_threadpool(_photo_notes, token, result)
    # Снимки из писем разбираются здесь же: сверка готова, излишки известны,
    # и архив письма после этого скачивается уже обработанным.
    await run_in_threadpool(_mail_vision, token, result)
    return _result_page(request, result, strict_on, verify_mode, logic=logic_percent)


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
    logic: int = DEFAULT_LOGIC,
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
            logic,
        )
    except (ParseError, RepairError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, str(error), strict_on, verify_mode, logic)
    except Exception:  # noqa: BLE001
        logger.exception("Неизвестная ошибка")
        shutil.rmtree(folder, ignore_errors=True)
        return _error(request, GENERIC_ERROR, strict_on, verify_mode, logic, status=500)

    # Разобранные снимки и ответы по фото переезжают на новый токен: правки
    # в сверке не должны стирать работу по фото.
    kept_photos = PHOTOS.get(token, [])
    kept_rows = set(PHOTO_ROWS.get(token, set()))
    kept_mail = MAIL_VISION.get(token)
    _forget(token)
    new_token = result.output_path.parent.name
    if kept_photos:
        PHOTOS[new_token] = kept_photos
    if kept_rows:
        PHOTO_ROWS[new_token] = kept_rows
    if kept_mail:
        MAIL_VISION[new_token] = kept_mail
    # Пересборка идёт от исходника и пишет столбец 12 заново, поэтому пометки
    # о фото возвращаются на место сразу после сборки.
    await run_in_threadpool(_photo_notes, new_token, result)
    # Повторно снимки в модель не гоняем: разбор нужен только тогда, когда
    # сверка его ещё не видела (например, почту забрали позже).
    if not MAIL_VISION.get(new_token):
        await run_in_threadpool(_mail_vision, new_token, result)
    return _result_page(request, result, strict_on, verify_mode, message, logic=logic)


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
    logic_percent = _percent(
        form.get("logic"), LOGIC_FLAGS.get(token, DEFAULT_LOGIC)
    )

    return await _rebuild(
        request,
        old,
        dict(old.decisions or {}),
        sheet_meta,
        strict_on,
        verify_mode,
        token,
        message="Данные сверки внесены в файл.",
        logic=logic_percent,
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
    logic_percent = _percent(
        form.get("logic"), LOGIC_FLAGS.get(token, DEFAULT_LOGIC)
    )

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
        logic=logic_percent,
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
    logic_percent = _percent(
        form.get("logic"), LOGIC_FLAGS.get(token, DEFAULT_LOGIC)
    )
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
        logic=logic_percent,
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
    note_refs: bool = True,
    logic: int = DEFAULT_LOGIC,
) -> HTMLResponse:
    token = result.output_path.parent.name
    RESULTS[token] = result
    STRICT_FLAGS[token] = bool(strict)
    VERIFY_FLAGS[token] = _mode(verify)
    LOGIC_FLAGS[token] = _percent(logic)
    # При простом открытии уже готовой сверки журнал справочников не трогаем.
    if note_refs:
        _note_refs(result)
    # Подтверждённые пары в таблице не показываются.
    pending = [row for row in result.doubtful if not row.get("answered")]
    note = _mail_note()
    mail_report = MAIL_VISION.get(token) or {}
    # Своё сообщение страницы важнее; если его нет — рассказываем про разбор
    # снимков из писем и про архив письма.
    if not message and note["letters"]:
        message = mail_vision.summary(mail_report)
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
            "logic": _percent(logic),
            "refs": result.refs,
            "meta": result.sheet_meta.to_form(),
            "comparison": result.comparison,
            "claims": result.claims,
            "message": message,
            "photos": len(PHOTOS.get(token, [])),
            "mail_note": note,
            "mail_letters": note["letters"],
            "mail_vision": mail_report,
        },
    )


@app.get("/result/{token}", response_class=HTMLResponse)
def result_page(request: Request, token: str):
    """Открывает сверку по её постоянному адресу.

    Благодаря этому переход на «Фото товара» и обратно не теряет работу:
    страница со спорными парами, ручными полями и заявками открывается
    в том же виде, в каком её оставили.
    """
    result = RESULTS.get(token)
    if result is None or not result.output_path.is_file():
        return _error(request, EXPIRED, status=404)
    return _result_page(
        request,
        result,
        STRICT_FLAGS.get(token, False),
        VERIFY_FLAGS.get(token, "off"),
        note_refs=False,
        logic=LOGIC_FLAGS.get(token, DEFAULT_LOGIC),
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


# --- Фото плюсующего товара ------------------------------------------------


def _vision_items(result: PipelineResult) -> list:
    """Позиции сверки для подбора по фото."""
    return vision_core.items_from_rows(result.clusters, tuple(settings.type_words))


def _photo_notes(token: str, result: PipelineResult) -> tuple[bool, str]:
    """Ставит в файл пометки «нет фото» и снимает их со строк с фото.

    Вызывается после каждой сборки файла: пересборка идёт от исходника и
    пишет столбец 12 заново, поэтому раньше пометки не доходили до
    скачанного файла.
    """
    ok, detail = photo_mark.apply(
        result.output_path,
        PHOTO_ROWS.get(token, set()),
        sheet_name=settings.sheet_name,
    )
    if not ok:
        logger.warning("Пометки о фото не записаны: %s", detail)
    return ok, detail


def _mail_vision(token: str, result: PipelineResult) -> dict:
    """Разбирает снимки писем сразу при обработке сверки.

    Снимки получают имя узнанного товара (это делает архив письма
    обработанным), а ответы модели попадают на страницу «Фото товара» по
    этому же токену: человек подтверждает строки руками, программа ничего
    за него не решает.
    """
    report = mail_vision.recognize_letters(
        MAIL_LETTERS, _vision_items(result), _base()
    )
    MAIL_VISION[token] = report
    found = [item for item in report.get("results") or [] if item.candidates]
    if found:
        kept = list(PHOTOS.get(token, []))
        known = {(item.photo, item.digest) for item in kept}
        for answer in found:
            key = (answer.photo, answer.digest)
            if key in known:
                continue
            known.add(key)
            kept.append(answer)
        PHOTOS[token] = kept
    # Имена, которые дала нейросеть, должны дожить до скачивания архива и
    # после перезапуска службы.
    if report.get("named"):
        mail_store.save(settings, MAIL_LETTERS)
    return report


def _vision_page(
    request: Request,
    message: str = "",
    error: str = "",
    results: list | None = None,
    token: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Страница распознавания: состояние, снимки и все настройки.

    Разобранные снимки берутся из памяти по токену сверки, поэтому
    подтверждение одного снимка не стирает остальные ответы модели.
    """
    base = _base()
    config = vision_core.load_config(base)
    jobs = _jobs(with_surplus=True)
    # Если сверка одна, показываем её снимки без лишнего выбора.
    if not token and len(jobs) == 1:
        token = jobs[0]["token"]
    shown = results if results is not None else PHOTOS.get(token, [])
    return templates.TemplateResponse(
        request=request,
        name="vision.html",
        context={
            "vision": vision_core.status(base, config),
            "config": config,
            "vision_max_photo_mb": config["vision_max_photo_mb"],
            "jobs": jobs,
            "token": token,
            "results": shown,
            "message": message,
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/vision", response_class=HTMLResponse)
def vision_page(request: Request, token: str = ""):
    """Страница фото. Токен в адресе открывает снимки нужной сверки."""
    return _vision_page(request, token=token if token in RESULTS else "")


@app.post("/vision/settings", response_class=HTMLResponse)
def vision_settings(
    request: Request,
    enabled: str | None = Form(default=None),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    max_rows: str | None = Form(default=None),
    max_photo_mb: str | None = Form(default=None),
    timeout: str | None = Form(default=None),
    retries: str | None = Form(default=None),
    pause_seconds: str | None = Form(default=None),
    text_min_score: str | None = Form(default=None),
    cache_limit: str | None = Form(default=None),
    token: str = Form(default=""),
):
    """Сохраняет настройки распознавания без перезапуска службы.

    Ключ API тоже задаётся здесь: пустое поле означает «оставить как было».
    """
    values = {
        "vision_enabled": _is_on(enabled),
        "vision_api_key": api_key,
        "vision_max_rows": max_rows,
        "vision_max_photo_mb": max_photo_mb,
        "vision_timeout": timeout,
        "vision_retries": retries,
        "vision_pause_seconds": pause_seconds,
        "vision_text_min_score": text_min_score,
        "vision_cache_limit": cache_limit,
    }
    if str(model or "").strip():
        values["vision_model"] = model
    try:
        vision_core.save_config(settings, values)
    except OSError as error:
        logger.exception("Настройки распознавания не сохранены")
        return _vision_page(
            request,
            error=(
                f"Настройки не сохранены: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}. Дайте службе право писать в эту папку."
            ),
            token=token,
            status_code=500,
        )
    return _vision_page(request, message="Настройки сохранены.", token=token)


@app.post("/vision/photos", response_class=HTMLResponse)
async def vision_photos(
    request: Request,
    token: str = Form(...),
    photos: list[UploadFile] = File(default=[]),
):
    """Разбирает снимки и предлагает строки из излишков текущей сверки."""
    base = _base()
    config = vision_core.load_config(base)
    result = RESULTS.get(token)
    if result is None or not result.output_path.is_file():
        return _vision_page(request, error=EXPIRED, status_code=400)
    if not config["vision_enabled"] or not config["vision_api_key"]:
        return _vision_page(
            request,
            error="Сначала включите распознавание и введите ключ API.",
            token=token,
            status_code=400,
        )

    sent_photos = [item for item in photos if item is not None and (item.filename or "").strip()]
    if not sent_photos:
        return _vision_page(request, error="Фото не выбраны.", token=token, status_code=400)

    folder = result.output_path.parent / "photos"
    saved: list[Path] = []
    for sent in sent_photos:
        name = _safe_name(sent.filename)
        if not vision_core.is_photo(name):
            return _vision_page(
                request,
                error=f"Файл {name} не похож на снимок: нужны jpg, png, webp или heic.",
                token=token,
                status_code=400,
            )
        target = folder / name
        try:
            size = await _save_upload(sent, target, int(config["vision_max_photo_mb"]))
        except UploadTooLarge:
            return _vision_page(
                request,
                error=f"Снимок {name} больше {config['vision_max_photo_mb']} МБ.",
                token=token,
                status_code=400,
            )
        except OSError:
            logger.exception("Снимок не сохранён")
            return _vision_page(request, error=GENERIC_ERROR, token=token, status_code=500)
        if size:
            saved.append(target)

    if not saved:
        return _vision_page(request, error=EMPTY_UPLOAD, token=token, status_code=400)

    try:
        found = await run_in_threadpool(
            vision_core.recognize_all, saved, _vision_items(result), base
        )
    except Exception:  # noqa: BLE001
        logger.exception("Разбор фото не удался")
        return _vision_page(request, error=GENERIC_ERROR, token=token, status_code=500)

    # Разобранное живёт до подтверждения каждого снимка.
    PHOTOS[token] = list(found)
    return _vision_page(
        request,
        message=(
            f"Разобрано снимков: {len(found)}. Подтверждайте по одному: остальные "
            "останутся на странице."
        ),
        token=token,
    )


def _drop_photo(token: str, photo: str, digest: str) -> None:
    """Убирает со страницы только тот снимок, по которому дан ответ."""
    kept = [
        item
        for item in PHOTOS.get(token, [])
        if not (
            item.photo == photo
            and (not digest or not item.digest or item.digest == digest)
        )
    ]
    if kept:
        PHOTOS[token] = kept
    else:
        PHOTOS.pop(token, None)


@app.post("/vision/confirm", response_class=HTMLResponse)
def vision_confirm(
    request: Request,
    token: str = Form(default=""),
    photo: str = Form(default=""),
    digest: str = Form(default=""),
    text: str = Form(default=""),
    row: str = Form(default="0"),
    name: str = Form(default=""),
    source: str = Form(default=""),
    picked: str = Form(default="on"),
):
    """Запоминает ответ человека по снимку и правит пометки в сверке.

    Уходит со страницы только этот снимок; остальные ответы модели остаются.
    Подтверждённая строка попадает в список строк с фото, и пометка
    «нет фото» с неё снимается; у остальных плюсующих строк она остаётся.
    """
    try:
        number = int(str(row or "0").strip() or 0)
    except ValueError:
        number = 0
    chosen = _is_on(picked) and number > 0

    vision_core.remember(
        settings,
        photo=photo,
        digest=digest,
        text=text,
        row=number,
        name=name,
        picked=chosen,
        source=source,
    )

    note = ""
    warning = ""
    result = RESULTS.get(token)
    if chosen and result is not None:
        PHOTO_ROWS.setdefault(token, set()).add(number)
        ok, detail = _photo_notes(token, result)
        if ok:
            note = (
                f" Пометка «нет фото» с этой строки снята. {detail}"
                " Скачайте файл заново, чтобы увидеть изменения."
            )
        else:
            warning = detail
    elif chosen and token:
        warning = EXPIRED

    _drop_photo(token, photo, digest)

    if chosen:
        message = f"Ответ записан: строка {number}, {name}.{note}"
    else:
        message = (
            "Ответ записан: ни одна из предложенных строк не подходит. "
            "Пометки «нет фото» остались на месте."
        )
    return _vision_page(request, message=message, error=warning, token=token)


@app.post("/vision/check", response_class=HTMLResponse)
def vision_check(request: Request, token: str = Form(default="")):
    """Проверяет ключ и модель коротким запросом без картинки.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток.
    """
    ok, note = vision_core.check(_base())
    if not ok:
        return _vision_page(request, error=note, token=token, status_code=400)
    return _vision_page(request, message=note, token=token)


@app.post("/vision/cache/clear", response_class=HTMLResponse)
def vision_cache_clear(request: Request, token: str = Form(default="")):
    """Забывает разобранные снимки: следующий разбор пойдёт заново."""
    forgotten = vision_core.clear_cache(_base())
    return _vision_page(
        request, message=f"Кеш очищен, забыто снимков: {forgotten}.", token=token
    )


@app.post("/vision/key/clear", response_class=HTMLResponse)
def vision_key_clear(request: Request, token: str = Form(default="")):
    """Удаляет ключ API из настроек."""
    vision_core.forget_key(settings)
    return _vision_page(request, message="Ключ API удалён из настроек.", token=token)


# --- Почта ревизоров -------------------------------------------------------


def _mail_note() -> dict:
    """Подсказки из разобранных писем.

    Этим пользуется страница готовой сверки: кнопки архивов писем,
    предупреждение о товаре в списке несосчитанного и ФИО ночного
    продавца для автоподстановки в форму.
    """
    letters: list[dict] = []
    in_list = False
    words: list[str] = []
    seller = ""
    for letter in MAIL_LETTERS:
        hints = getattr(letter, "hints", None) or {}
        if hints.get("in_list"):
            in_list = True
            for word in hints.get("list_words") or []:
                if word not in words:
                    words.append(word)
        if not seller and hints.get("seller"):
            seller = str(hints.get("seller"))
        letters.append(
            {
                "uid": str(getattr(letter, "uid", "")),
                "subject": str(getattr(letter, "subject", "") or "без темы"),
                "store": str(hints.get("store") or ""),
                "in_list": bool(hints.get("in_list")),
                "seller": str(hints.get("seller") or ""),
            }
        )
    return {"letters": letters, "in_list": in_list, "list_words": words, "seller": seller}


def _letter_by_uid(uid: str):
    """Письмо по его номеру в ящике.

    Сначала смотрим в памяти, затем на диске: после перезапуска службы
    список писем восстанавливается из `mail-letters.json`, и кнопка архива
    на странице сверки должна работать без нового захода в ящик.
    """
    wanted = str(uid or "").strip()
    for letter in MAIL_LETTERS:
        if str(getattr(letter, "uid", "")) == wanted:
            return letter
    for letter in mail_store.load(settings):
        if str(getattr(letter, "uid", "")) == wanted:
            MAIL_LETTERS.append(letter)
            return letter
    return None


def _name_photos(letter, token: str = "") -> None:
    """Даёт снимкам письма имя товара, который узнала нейросеть.

    Обычно имена уже проставлены при обработке сверки (`_mail_vision`), и
    тогда эта работа пропускается: гонять снимки в модель второй раз при
    каждом скачивании архива незачем. Нет ключа, выключено распознавание
    или нет сверки — снимки остаются со своими именами: архив всё равно
    должен скачаться.
    """
    photos = [item for item in (letter.photos or []) if item.path]
    if not photos:
        return
    if mail_vision.named(letter) >= len(photos):
        return
    base = _base()
    config = vision_core.load_config(base)
    if not config["vision_enabled"] or not config["vision_api_key"]:
        return
    result = RESULTS.get(token)
    items = _vision_items(result) if result is not None else []
    if not items:
        return
    paths = [Path(item.path) for item in photos if Path(item.path).is_file()]
    if not paths:
        return
    try:
        found = vision_core.recognize_all(paths, items, base)
    except Exception:  # noqa: BLE001
        logger.exception("Снимки письма не разобраны")
        return
    by_name = {item.photo: item for item in found}
    named = 0
    for photo in photos:
        answer = by_name.get(Path(photo.path).name)
        best = answer.best if answer is not None else None
        if best is not None and best.name:
            photo.title = best.name
            named += 1
    if named:
        mail_store.save(settings, MAIL_LETTERS)


def _mail_page(
    request: Request,
    message: str = "",
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """Страница почты: состояние ящика, разобранные письма, настройки."""
    return templates.TemplateResponse(
        request=request,
        name="mail.html",
        context={
            "mail": mail_core.status(settings),
            "letters": MAIL_LETTERS,
            "skipped": MAIL_SKIPPED,
            "message": message,
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/mail", response_class=HTMLResponse)
def mail_page(request: Request):
    """Раздел «Почта ревизоров». Письма забираются только по кнопке."""
    return _mail_page(request)


@app.post("/mail/settings")
def mail_settings(
    request: Request,
    enabled: str | None = Form(default=None),
    host: str | None = Form(default=None),
    port: str | None = Form(default=None),
    login: str | None = Form(default=None),
    password: str | None = Form(default=None),
    folder: str | None = Form(default=None),
    senders: str | None = Form(default=None),
    since_days: str | None = Form(default=None),
    max_letters: str | None = Form(default=None),
    max_photos: str | None = Form(default=None),
    max_photo_mb: str | None = Form(default=None),
    timeout: str | None = Form(default=None),
    keep_days: str | None = Form(default=None),
    only_unseen: str | None = Form(default=None),
    mark_seen: str | None = Form(default=None),
):
    """Сохраняет настройки ящика без перезапуска службы.

    Пустое поле пароля означает «оставить как было»: вводить его заново при
    каждой правке не нужно. Пустой список адресов — особый случай: его
    записываем принудительно, иначе белый список нельзя было бы снять.
    """
    values: dict[str, object] = {
        "mail_enabled": _is_on(enabled),
        "mail_only_unseen": _is_on(only_unseen),
        "mail_mark_seen": _is_on(mark_seen),
        "mail_host": host,
        "mail_port": port,
        "mail_login": login,
        "mail_password": password,
        "mail_folder": folder,
        "mail_senders": senders,
        "mail_since_days": since_days,
        "mail_max_letters": max_letters,
        "mail_max_photos": max_photos,
        "mail_max_photo_mb": max_photo_mb,
        "mail_timeout": timeout,
        "mail_keep_days": keep_days,
    }
    try:
        mail_core.save_config(settings, values)
        if senders is not None and not str(senders).strip():
            path = runtime.runtime_path(settings)
            stored = runtime.load(path)
            stored["mail_senders"] = ""
            runtime.save(stored, path)
    except OSError as error:
        logger.exception("Настройки почты не сохранены")
        return _mail_page(
            request,
            error=(
                f"Настройки не сохранены: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}. Дайте службе право писать в эту папку."
            ),
            status_code=500,
        )
    return RedirectResponse(url="/mail", status_code=303)


@app.post("/mail/check", response_class=HTMLResponse)
def mail_check(request: Request):
    """Проверяет вход в ящик и считает письма к разбору. Ничего не скачивает.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток, поэтому
    медленный почтовый сервер не держит остальные страницы.
    """
    ok, note = mail_core.check(settings)
    if not ok:
        return _mail_page(request, error=note, status_code=400)
    return _mail_page(request, message=note)


@app.post("/mail/fetch", response_class=HTMLResponse)
async def mail_fetch(request: Request):
    """Забирает письма ревизоров и складывает вложения на диск.

    Заход идёт по кнопке: состояние сверок живёт в памяти процесса, и
    фоновому сбору было бы некуда складывать результат. Разобранные письма
    складываются к прежним и пишутся на диск, поэтому кнопки архивов не
    пропадают, когда новых писем в ящике нет.
    """
    try:
        report = await run_in_threadpool(mail_core.collect, settings)
    except mail_core.MailError as error:
        return _mail_page(request, error=str(error), status_code=400)
    except OSError as error:
        logger.exception("Снимки из почты не сохранены")
        return _mail_page(
            request,
            error=f"Снимки не сохранены: {error}. Проверьте права на папку данных.",
            status_code=500,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Заход в почту не удался")
        return _mail_page(
            request,
            error="Заход в почту не удался. Подробности — в журнале службы.",
            status_code=500,
        )

    fresh = list(report.get("letters") or [])
    kept = await run_in_threadpool(mail_store.remember, settings, fresh)
    MAIL_LETTERS[:] = kept
    MAIL_SKIPPED[:] = list(report.get("skipped") or [])
    cleaned = int(report.get("cleaned") or 0)

    if not fresh:
        message = (
            "Новых писем нет. Уже разобранные письма второй раз не тянутся: "
            "их номера хранятся в mail-state.json."
        )
        if kept:
            message += f" Письма прошлых заходов на месте: {len(kept)}."
    else:
        message = (
            f"Разобрано писем: {len(fresh)}, снимков скачано: "
            f"{int(report.get('photos') or 0)}."
        )
    if cleaned:
        message += f" Удалено старых снимков: {cleaned}."
    return _mail_page(request, message=message)


@app.post("/mail/password/clear", response_class=HTMLResponse)
def mail_password_clear(request: Request):
    """Удаляет пароль приложения из настроек."""
    try:
        mail_core.forget_password(settings)
    except OSError as error:
        logger.exception("Пароль ящика не удалён")
        return _mail_page(
            request,
            error=(
                f"Пароль не удалён: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}."
            ),
            status_code=500,
        )
    return _mail_page(request, message="Пароль ящика удалён из настроек.")


@app.get("/mail/photo/{uid}/{name}")
def mail_photo(uid: str, name: str):
    """Отдаёт скачанный из письма снимок.

    На диске файл лежит с номером по порядку («01-photo.jpg»), а в письме у
    него своё имя, поэтому ищем и по тому, и по другому. Имена чистятся
    `safe_name`, а путь дополнительно сверяется с папкой снимков: письмо
    может принести «../../etc/passwd».
    """
    root = mail_core.photos_dir(settings)
    wanted = mail_core.safe_name(name)
    folder = root / mail_core.safe_name(uid)
    target: Path | None = None
    if folder.is_dir():
        for file in sorted(folder.iterdir()):
            if not file.is_file():
                continue
            if file.name == wanted or file.name.split("-", 1)[-1] == wanted:
                target = file
                break
    if target is None:
        return HTMLResponse(
            "Снимок не найден: возможно, он уже удалён по сроку хранения.",
            status_code=404,
        )
    try:
        inside = target.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        return HTMLResponse("Снимок не найден.", status_code=404)
    return FileResponse(path=target, filename=wanted)


@app.get("/mail/archive/{uid}")
async def mail_archive(uid: str, token: str = ""):
    """Отдаёт обработанный архив письма: вложения и текст письма отдельным файлом.

    Снимки в архиве названы товаром, который узнала нейросеть при обработке
    сверки. Если имён почему-то нет (например, почту забрали позже), они
    проставляются здесь. Нет ключа или нет сверки — файлы сохраняют свои
    имена, архив всё равно собирается.
    """
    letter = _letter_by_uid(uid)
    if letter is None:
        return HTMLResponse(
            "Письмо не найдено: заберите почту заново на странице «Почта ревизоров».",
            status_code=404,
        )
    try:
        await run_in_threadpool(_name_photos, letter, token)
        archive = await run_in_threadpool(mail_core.build_archive, settings, letter)
    except OSError as error:
        logger.exception("Архив письма не собран")
        return HTMLResponse(
            f"Архив не собран: {error}. Проверьте права на папку данных.",
            status_code=400,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Архив письма не собран")
        return HTMLResponse(
            "Архив не собран. Подробности — в журнале службы.",
            status_code=400,
        )
    return FileResponse(
        path=archive,
        filename=mail_core.archive_name(letter),
        media_type="application/zip",
    )


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
    base = _base()
    return templates.TemplateResponse(
        request=request,
        name="training.html",
        context={
            "model": model_status(settings),
            "embed": runtime.embed_status(base),
            "judge": runtime.judge_status(base),
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
def training_embed(
    request: Request,
    embed: str | None = Form(default=None),
    provider: str | None = Form(default=None),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    timeout: str | None = Form(default=None),
    max_requests: str | None = Form(default=None),
):
    """Сохраняет настройки эмбеддингов имён без перезапуска службы.

    Провайдер выбирается здесь же: файл ONNX на сервере или OpenRouter по
    сети. Ключ задаётся только в интерфейсе, пустое поле означает
    «оставить как было».
    """
    values: dict[str, object] = {
        "embed_enabled": _is_on(embed),
        "embed_api_key": api_key,
        "embed_timeout": timeout,
        "embed_max_requests": max_requests,
    }
    if str(provider or "").strip():
        values["embed_provider"] = provider
    if str(model or "").strip():
        values["embed_model"] = model
    try:
        runtime.save_embed(settings, values)
    except OSError as error:
        logger.exception("Настройки эмбеддингов не сохранены")
        return _training_page(
            request,
            error=(
                f"Настройки не сохранены: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}. Дайте службе право писать в эту папку."
            ),
            status_code=500,
        )
    return RedirectResponse(url="/training", status_code=303)


@app.post("/training/embed/check", response_class=HTMLResponse)
def training_embed_check(request: Request):
    """Проверяет ключ и модель эмбеддингов одним коротким запросом.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток.
    """
    ok, note = embed_core.check_remote(_base())
    if not ok:
        return _training_page(request, error=note, status_code=400)
    return _training_page(request, message=note)


@app.post("/training/embed/key/clear", response_class=HTMLResponse)
def training_embed_key_clear(request: Request):
    """Удаляет ключ сервиса эмбеддингов из настроек."""
    try:
        runtime.forget_embed_key(settings)
    except OSError as error:
        logger.exception("Ключ эмбеддингов не удалён")
        return _training_page(
            request,
            error=(
                f"Ключ не удалён: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}."
            ),
            status_code=500,
        )
    return _training_page(request, message="Ключ сервиса эмбеддингов удалён из настроек.")


@app.post("/training/judge")
def training_judge(
    request: Request,
    provider: str | None = Form(default=None),
    api_key: str | None = Form(default=None),
    model: str | None = Form(default=None),
    api_url: str | None = Form(default=None),
    timeout: str | None = Form(default=None),
    retries: str | None = Form(default=None),
    max_requests: str | None = Form(default=None),
    batch: str | None = Form(default=None),
    max_pairs: str | None = Form(default=None),
    logic: str | None = Form(default=None),
):
    """Сохраняет настройки LLM-судьи без перезапуска службы.

    Провайдер выбирается здесь же: OpenRouter или GigaChat. Ключ задаётся
    только здесь; пустое поле означает «оставить как было». Влияние логики
    здесь — значение по умолчанию для страницы загрузки.
    """
    values: dict[str, object] = {
        "judge_api_key": api_key,
        "judge_timeout": timeout,
        "judge_retries": retries,
        "judge_max_requests": max_requests,
        "judge_batch": batch,
        "judge_max_pairs": max_pairs,
    }
    if str(provider or "").strip():
        values["judge_provider"] = provider
    if str(model or "").strip():
        values["judge_model"] = model
    if str(api_url or "").strip():
        values["judge_api_url"] = api_url
    if str(logic if logic is not None else "").strip():
        values["logic_weight"] = _percent(logic) / 100.0
    try:
        runtime.save_judge(settings, values)
    except OSError as error:
        logger.exception("Настройки LLM не сохранены")
        return _training_page(
            request,
            error=(
                f"Настройки не сохранены: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}. Дайте службе право писать в эту папку."
            ),
            status_code=500,
        )
    return RedirectResponse(url="/training", status_code=303)


@app.post("/training/judge/check", response_class=HTMLResponse)
def training_judge_check(request: Request):
    """Проверяет ключ и модель LLM одной пробной парой.

    Обработчик синхронный: FastAPI сам уносит его в отдельный поток.
    """
    ok, note = judge_core.check_key(_base())
    if not ok:
        return _training_page(request, error=note, status_code=400)
    return _training_page(request, message=note)


@app.post("/training/judge/key/clear", response_class=HTMLResponse)
def training_judge_key_clear(request: Request):
    """Удаляет ключ провайдера LLM из настроек."""
    try:
        runtime.forget_judge_key(settings)
    except OSError as error:
        logger.exception("Ключ LLM не удалён")
        return _training_page(
            request,
            error=(
                f"Ключ не удалён: {error}. Файл настроек — "
                f"{runtime.runtime_path(settings)}."
            ),
            status_code=500,
        )
    return _training_page(request, message="Ключ провайдера LLM удалён из настроек.")


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
    logic: int = DEFAULT_LOGIC,
    status: int = 400,
) -> HTMLResponse:
    base = _base()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": message,
            "max_upload_mb": settings.max_upload_mb,
            "strict": bool(strict),
            "verify": _mode(verify),
            "logic": _percent(logic),
            "model": model_status(settings),
            "embed": runtime.embed_status(base),
            "judge": runtime.judge_status(base),
            "jobs": _jobs(),
        },
        status_code=status,
    )
