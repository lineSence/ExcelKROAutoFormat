"""Ход разбора сверки: этапы, проценты и время каждого шага.

Раньше страница загрузки просто висела до конца работы: человек не видел,
что именно идёт долго — ремонт файла, подбор пересортов, справочники или
снимки из писем. Теперь каждый шаг отмечается здесь, а страница
`/upload/progress/{билет}` показывает их списком с полосой хода работы.

Журнал живёт в памяти процесса: это подсказка человеку, а не данные.
После перезапуска службы билет пропадает — страница честно скажет об этом
и предложит загрузить файл заново. Исключений наружу модуль не отдаёт:
неизвестный билет — это `None`, а не ошибка, иначе страница падала бы с
Internal server error.

Этапы задаёт веб-слой парами «ключ, название». Порядок важен: проценты
считаются как доля завершённых этапов.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

# Сколько разборов помним и как долго. Это только показ хода работы,
# поэтому список короткий: память сервера дороже старых билетов.
LIMIT = 20
KEEP_SECONDS = 3600
# Этап дольше этого времени считается подозрительно долгим: страница
# подсказывает, где именно всё встало.
SLOW_SECONDS = 180

WAITING = "ждёт"
RUNNING = "идёт"
DONE = "готово"
SKIPPED = "пропущено"
FAILED = "сбой"

_lock = threading.Lock()
_jobs: dict[str, "Job"] = {}


@dataclass
class Stage:
    """Один этап разбора."""

    key: str
    title: str
    status: str = WAITING
    note: str = ""
    seconds: float = 0.0
    started: float = 0.0


@dataclass
class Job:
    """Разбор одного файла: этапы, итоговый токен сверки и ошибка."""

    ticket: str
    stages: list[Stage]
    started: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    # Токен готовой сверки: появляется, как только файл собран, ещё до
    # конца необязательных этапов (снимки писем).
    token: str = ""
    error: str = ""
    finished: bool = False


def _clean() -> None:
    """Убирает старые билеты. Вызывается под замком."""
    now = time.monotonic()
    for ticket in list(_jobs):
        job = _jobs[ticket]
        if now - job.updated > KEEP_SECONDS:
            _jobs.pop(ticket, None)
    while len(_jobs) > LIMIT:
        oldest = min(_jobs.values(), key=lambda item: item.updated)
        _jobs.pop(oldest.ticket, None)


def start(stages) -> str:
    """Начинает разбор и возвращает билет для адреса страницы."""
    ticket = uuid.uuid4().hex
    steps = [Stage(key=str(key), title=str(title)) for key, title in stages]
    with _lock:
        _jobs[ticket] = Job(ticket=ticket, stages=steps)
        _clean()
    return ticket


def _find(ticket: str, key: str) -> tuple["Job | None", "Stage | None"]:
    job = _jobs.get(ticket)
    if job is None:
        return None, None
    for stage in job.stages:
        if stage.key == key:
            return job, stage
    return job, None


def begin(ticket: str, key: str) -> None:
    """Отмечает начало этапа."""
    with _lock:
        job, stage = _find(ticket, key)
        if job is None or stage is None:
            return
        stage.status = RUNNING
        stage.started = time.monotonic()
        job.updated = time.monotonic()


def _close(ticket: str, key: str, status: str, note: str) -> None:
    with _lock:
        job, stage = _find(ticket, key)
        if job is None or stage is None:
            return
        now = time.monotonic()
        if stage.started:
            stage.seconds = round(now - stage.started, 1)
        stage.status = status
        if note:
            stage.note = note
        job.updated = now


def done(ticket: str, key: str, note: str = "") -> None:
    """Этап закончен успешно."""
    _close(ticket, key, DONE, note)


def skip(ticket: str, key: str, note: str = "") -> None:
    """Этап не понадобился: например, писем нет."""
    _close(ticket, key, SKIPPED, note)


def stage_failed(ticket: str, key: str, note: str = "") -> None:
    """Этап не удался, но работа продолжается."""
    _close(ticket, key, FAILED, note)


def ready(ticket: str, token: str) -> None:
    """Запоминает токен готовой сверки: страницу уже можно открыть."""
    with _lock:
        job = _jobs.get(ticket)
        if job is None:
            return
        job.token = str(token or "")
        job.updated = time.monotonic()


def fail(ticket: str, message: str) -> None:
    """Разбор оборвался: текст ошибки показывается человеку."""
    with _lock:
        job = _jobs.get(ticket)
        if job is None:
            return
        now = time.monotonic()
        for stage in job.stages:
            if stage.status == RUNNING:
                if stage.started:
                    stage.seconds = round(now - stage.started, 1)
                stage.status = FAILED
        job.error = str(message or "")
        job.finished = True
        job.updated = now


def finish(ticket: str, token: str = "") -> None:
    """Разбор закончен: страница уходит на готовую сверку."""
    with _lock:
        job = _jobs.get(ticket)
        if job is None:
            return
        if token:
            job.token = str(token)
        job.finished = True
        job.updated = time.monotonic()


def drop(ticket: str) -> None:
    with _lock:
        _jobs.pop(ticket, None)


def view(ticket: str) -> dict | None:
    """Ход работы для страницы и для JSON. Неизвестный билет — `None`."""
    with _lock:
        job = _jobs.get(ticket)
        if job is None:
            return None
        now = time.monotonic()
        stages: list[dict] = []
        current = ""
        stage_seconds = 0.0
        for stage in job.stages:
            seconds = stage.seconds
            if stage.status == RUNNING and stage.started:
                seconds = round(now - stage.started, 1)
                current = stage.title
                stage_seconds = seconds
            stages.append(
                {
                    "key": stage.key,
                    "title": stage.title,
                    "status": stage.status,
                    "note": stage.note,
                    "seconds": seconds,
                }
            )
        closed = sum(
            1 for stage in job.stages if stage.status in (DONE, SKIPPED, FAILED)
        )
        total = len(job.stages) or 1
        percent = 100 if job.finished else min(99, int(round(closed * 100 / total)))
        return {
            "ticket": job.ticket,
            "stages": stages,
            "percent": percent,
            "stage": current,
            "stage_seconds": stage_seconds,
            "seconds": round(now - job.started, 1),
            "token": job.token,
            "error": job.error,
            "finished": job.finished,
            "slow": bool(current and stage_seconds > SLOW_SECONDS),
        }
