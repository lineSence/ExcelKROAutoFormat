from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

LIMIT = 20
KEEP_SECONDS = 3600
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
    key: str
    title: str
    status: str = WAITING
    note: str = ""
    seconds: float = 0.0
    started: float = 0.0

@dataclass
class Job:
    ticket: str
    stages: list[Stage]
    started: float = field(default_factory=time.monotonic)
    updated: float = field(default_factory=time.monotonic)
    token: str = ""
    error: str = ""
    finished: bool = False

def _clean() -> None:
    now = time.monotonic()
    for ticket in list(_jobs):
        if now - _jobs[ticket].updated > KEEP_SECONDS:
            _jobs.pop(ticket, None)
    while len(_jobs) > LIMIT:
        oldest = min(_jobs.values(), key=lambda item: item.updated)
        _jobs.pop(oldest.ticket, None)

def start(stages) -> str:
    ticket = uuid.uuid4().hex
    steps = [Stage(key=str(key), title=str(title)) for key, title in stages]
    with _lock:
        _jobs[ticket] = Job(ticket=ticket, stages=steps)
        _clean()
    return ticket

def _find(ticket: str, key: str):
    job = _jobs.get(ticket)
    if job is None:
        return None, None
    for stage in job.stages:
        if stage.key == key:
            return job, stage
    return job, None

def begin(ticket: str, key: str) -> None:
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
    _close(ticket, key, DONE, note)

def skip(ticket: str, key: str, note: str = "") -> None:
    _close(ticket, key, SKIPPED, note)

def stage_failed(ticket: str, key: str, note: str = "") -> None:
    _close(ticket, key, FAILED, note)

def ready(ticket: str, token: str) -> None:
    with _lock:
        job = _jobs.get(ticket)
        if job is None:
            return
        job.token = str(token or "")
        job.updated = time.monotonic()

def fail(ticket: str, message: str) -> None:
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
            stages.append({"key": stage.key, "title": stage.title, "status": stage.status, "note": stage.note, "seconds": seconds})
        closed = sum(1 for stage in job.stages if stage.status in (DONE, SKIPPED, FAILED))
        total = len(job.stages) or 1
        percent = 100 if job.finished else min(99, int(round(closed * 100 / total)))
        return {"ticket": job.ticket, "stages": stages, "percent": percent, "stage": current, "stage_seconds": stage_seconds, "seconds": round(now - job.started, 1), "token": job.token, "error": job.error, "finished": job.finished, "slow": bool(current and stage_seconds > SLOW_SECONDS)}
