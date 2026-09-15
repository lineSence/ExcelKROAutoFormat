from __future__ import annotations
import dataclasses,logging
from pathlib import Path
from fastapi.templating import Jinja2Templates
from .config import Settings
from .core import runtime
from .state.jobs import JobStore
from .state.letters import LetterStore
BASE_DIR=Path(__file__).resolve().parent
settings=Settings.load()
logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger("excelkro")
templates=Jinja2Templates(directory=str(BASE_DIR/"templates"))
jobs=JobStore(settings); letters=LetterStore(settings)
def base()->Settings: return runtime.apply(settings)
def settings_for(strict:bool,verify:str,logic:int)->Settings:
    return dataclasses.replace(base(),strict_resort=bool(strict),verify_mode=str(verify),logic_weight=max(0,min(100,int(logic)))/100)
