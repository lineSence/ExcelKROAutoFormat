from __future__ import annotations
import asyncio
from collections.abc import Coroutine
from typing import Any
_TASKS:set[asyncio.Task]=set()
def background(coro:Coroutine[Any,Any,Any])->asyncio.Task:
    task=asyncio.create_task(coro); _TASKS.add(task); task.add_done_callback(_TASKS.discard); return task
def running()->int:return len(_TASKS)
