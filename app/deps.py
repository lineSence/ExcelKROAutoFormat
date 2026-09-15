from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import Settings
from .core import runtime
from .state.jobs import JobStore
from .state.letters import LetterStore
from .web.forms import mode, percent

BASE_DIR = Path(__file__).resolve().parent
settings = Settings.load()
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("excelkro")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
jobs = JobStore(settings)
letters = LetterStore(settings)

def base() -> Settings:
    return runtime.apply(settings)

def settings_for(strict: bool, verify: str = "off", logic: int = 100) -> Settings:
    return dataclasses.replace(
        base(),
        strict_resort=bool(strict),
        verify_mode=mode(verify),
        logic_weight=percent(logic) / 100.0,
    )
