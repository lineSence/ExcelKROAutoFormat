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
    progress._jobs.clear()
    tickets = [progress.start(STAGES) for _ in range(progress.LIMIT + 5)]
    assert len(progress._jobs) == progress.LIMIT
    assert progress.view(tickets[0]) is None
    assert progress.view(tickets[-1]) is not None


def test_drop_removes_ticket() -> None:
    ticket = progress.start(STAGES)
    progress.drop(ticket)
    assert progress.view(ticket) is None