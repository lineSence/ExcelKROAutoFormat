"""Тесты журнала хода разбора сверки (`app/core/progress.py`).

Журнал живёт в памяти процесса и наружу отдаёт только словари: неизвестный
билет — это `None`, а не исключение, иначе страница падала бы с
Internal server error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import progress

STAGES = (("save", "Файл принят"), ("parse", "Разбор сверки"))


@pytest.fixture(autouse=True)
def _clean_jobs():
    """Каждый тест начинает с пустого журнала и не оставляет билетов."""
    progress._jobs.clear()
    yield
    progress._jobs.clear()


def test_unknown_ticket_is_none() -> None:
    """Неизвестный билет — `None`, а вызовы по нему молча пропускаются."""
    assert progress.view("нет-такого") is None
    progress.begin("нет-такого", "parse")
    progress.done("нет-такого", "parse")
    progress.skip("нет-такого", "parse")
    progress.stage_failed("нет-такого", "parse")
    progress.ready("нет-такого", "токен")
    progress.fail("нет-такого", "беда")
    progress.finish("нет-такого")
    progress.drop("нет-такого")


def test_stages_go_in_order() -> None:
    """Проценты считаются по закрытым этапам, итог — 100 после finish."""
    ticket = progress.start(STAGES)
    job = progress.view(ticket)
    assert job is not None
    assert job["percent"] == 0
    assert [step["status"] for step in job["stages"]] == [
        progress.WAITING,
        progress.WAITING,
    ]
    assert job["finished"] is False
    assert job["token"] == ""

    progress.done(ticket, "save", "файл.xlsx, 1,0 МБ")
    progress.begin(ticket, "parse")
    job = progress.view(ticket)
    assert job["percent"] == 50
    assert job["stage"] == "Разбор сверки"
    assert job["stages"][0]["note"] == "файл.xlsx, 1,0 МБ"
    assert job["stages"][0]["status"] == progress.DONE

    progress.done(ticket, "parse", "файл собран")
    progress.finish(ticket, "токен")
    job = progress.view(ticket)
    assert job["percent"] == 100
    assert job["finished"] is True
    assert job["token"] == "токен"
    assert job["stage"] == ""


def test_ready_shows_token_before_finish() -> None:
    """Готовая сверка доступна, пока идут необязательные этапы."""
    ticket = progress.start(STAGES)
    progress.ready(ticket, "токен")
    job = progress.view(ticket)
    assert job["token"] == "токен"
    assert job["finished"] is False


def test_fail_marks_running_stage() -> None:
    """Ошибка разбора: идущий этап сбойный, текст уходит на страницу."""
    ticket = progress.start(STAGES)
    progress.done(ticket, "save")
    progress.begin(ticket, "parse")
    progress.fail(ticket, "файл не читается")
    job = progress.view(ticket)
    assert job["error"] == "файл не читается"
    assert job["finished"] is True
    assert job["stages"][0]["status"] == progress.DONE
    assert job["stages"][1]["status"] == progress.FAILED


def test_skip_and_stage_failed_close_stage() -> None:
    """Пропущенный и сбойный этапы считаются закрытыми для полосы."""
    ticket = progress.start(STAGES)
    progress.skip(ticket, "save", "не понадобился")
    progress.begin(ticket, "parse")
    progress.stage_failed(ticket, "parse", "пометки не записаны")
    job = progress.view(ticket)
    # Разбор ещё не закончен (finish не вызывали), но оба этапа закрыты.
    assert job["percent"] == 99
    assert job["stages"][0]["status"] == progress.SKIPPED
    assert job["stages"][1]["status"] == progress.FAILED
    assert job["stages"][1]["note"] == "пометки не записаны"


def test_slow_stage_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Этап дольше предела подсвечивается предупреждением на странице."""
    monkeypatch.setattr(progress, "SLOW_SECONDS", -1)
    ticket = progress.start(STAGES)
    progress.begin(ticket, "parse")
    job = progress.view(ticket)
    assert job["slow"] is True
    assert job["stage"] == "Разбор сверки"
    assert job["stage_seconds"] >= 0


def test_old_tickets_are_evicted() -> None:
    """Журнал короткий: лишние билеты вытесняются новыми."""
    tickets = [progress.start(STAGES) for _ in range(progress.LIMIT + 5)]
    assert len(progress._jobs) == progress.LIMIT
    assert progress.view(tickets[0]) is None
    assert progress.view(tickets[-1]) is not None


def test_drop_removes_ticket() -> None:
    ticket = progress.start(STAGES)
    progress.drop(ticket)
    assert progress.view(ticket) is None
