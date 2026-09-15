from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from ..core import progress
from ..core.meta import SheetMeta
from ..core.parse import ParseError
from ..core.pipeline import PipelineResult, process
from ..core.repair import RepairError
from ..state.jobs import Job, JobStore
from ..state.letters import LetterStore
from ..web.forms import DEFAULT_LOGIC
from ..web.messages import GENERIC_ERROR
from .letters import mail_vision_for
from .photos import photo_notes
from .refs_notes import note_refs

logger = logging.getLogger("excelkro")

UPLOAD_STAGES = (
    ("save", "Файл принят и записан на диск"),
    ("parse", "Разбор сверки: ремонт файла, пересорты, справочники"),
    ("photo", "Пометки «нет фото» в готовом файле"),
    ("mail", "Снимки из писем ревизоров в нейросеть"),
)


class RebuildFailed(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


async def run_pipeline(
    source: Path,
    original_name: str,
    request_settings,
    folder: Path,
    decisions: dict[str, bool] | None = None,
    sheet_meta: SheetMeta | None = None,
    prev_path: Path | None = None,
    prev_name: str = "",
    claim_decisions: dict[str, bool] | None = None,
) -> PipelineResult:
    return await run_in_threadpool(
        process,
        source,
        original_name,
        request_settings,
        decisions,
        folder,
        sheet_meta,
        prev_path,
        prev_name,
        claim_decisions,
    )


async def run_upload_job(
    ticket: str,
    source: Path,
    name: str,
    strict: bool,
    verify: str,
    logic: int,
    folder: Path,
    prev_path: Path | None,
    prev_name: str,
    *,
    jobs: JobStore,
    letters: LetterStore,
    settings,
    settings_for: Callable,
    base: Callable,
) -> None:
    try:
        progress.begin(ticket, "parse")
        result = await run_pipeline(
            source,
            name,
            settings_for(strict, verify, logic),
            folder,
            prev_path=prev_path,
            prev_name=prev_name,
        )
        progress.done(ticket, "parse", f"файл собран, спорных пар: {len(result.doubtful or [])}")
    except (ParseError, RepairError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        progress.stage_failed(ticket, "parse", str(error))
        progress.fail(ticket, str(error))
        return
    except Exception:  # noqa: BLE001
        logger.exception("Разбор сверки не удался")
        shutil.rmtree(folder, ignore_errors=True)
        progress.stage_failed(ticket, "parse", GENERIC_ERROR)
        progress.fail(ticket, GENERIC_ERROR)
        return

    note_refs(result, settings)
    job = jobs.remember(result, strict, verify, logic)
    progress.ready(ticket, job.token)

    # Готовый файл не должен ждать сетевой разбор фотографий.
    progress.begin(ticket, "photo")
    try:
        ok, detail = photo_notes(job, settings)
        if ok:
            progress.done(ticket, "photo", detail)
        else:
            progress.stage_failed(ticket, "photo", detail)
    except Exception:  # noqa: BLE001
        logger.exception("Пометки о фото не записаны")
        progress.stage_failed(ticket, "photo", "пометки не записаны")

    # Почта — необязательный сетевой этап. Он идёт после того, как сверка
    # уже доступна по постоянному адресу.
    progress.begin(ticket, "mail")
    try:
        if letters.items:
            report = await mail_vision_for(job, letters, settings, base)
            note = str(report.get("note") or "") or "писем для разбора нет"
            if report.get("photos"):
                progress.done(ticket, "mail", note)
            else:
                progress.skip(ticket, "mail", note)
        else:
            progress.skip(ticket, "mail", "Письма не загружены.")
    except Exception:  # noqa: BLE001
        logger.exception("Снимки писем не разобраны")
        progress.stage_failed(
            ticket,
            "mail",
            "разбор снимков не удался, сверка при этом готова",
        )

    progress.finish(ticket)
    logger.info("Сверка готова: %s", job.token)


async def rebuild(
    job: Job,
    decisions: dict[str, bool],
    sheet_meta: SheetMeta | None,
    strict: bool,
    verify: str,
    logic: int = DEFAULT_LOGIC,
    claim_decisions: dict[str, bool] | None = None,
    *,
    jobs: JobStore,
    letters: LetterStore,
    settings,
    settings_for: Callable,
    base: Callable,
) -> Job:
    old = job.result
    folder = old.output_path.parent
    try:
        fresh = await run_pipeline(
            old.source_path,
            old.source_name,
            settings_for(strict, verify, logic),
            folder,
            decisions,
            sheet_meta,
            old.prev_path,
            old.prev_name,
            claim_decisions,
        )
    except (ParseError, RepairError) as error:
        raise RebuildFailed(str(error)) from error
    except Exception as error:  # noqa: BLE001
        logger.exception("Пересборка файла не удалась")
        raise RebuildFailed(GENERIC_ERROR, 500) from error

    note_refs(fresh, settings)
    return jobs.replace(job.token, fresh, strict, verify, logic)
