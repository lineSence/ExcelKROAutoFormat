from __future__ import annotations
import threading,uuid
from collections.abc import Callable,Iterator
from dataclasses import dataclass,field
from datetime import datetime,timedelta
from typing import Any
from ..core.pipeline import PipelineResult,sweep
@dataclass
class Job:
    token:str; result:PipelineResult; strict:bool=False; verify:str="off"; logic:int=100
    photos:list[dict]=field(default_factory=list); photo_rows:set[int]=field(default_factory=set); mail_vision:dict=field(default_factory=dict)
    @property
    def alive(self)->bool:return self.result.output_path.exists()
    @property
    def warehouse(self)->str:return str(getattr(self.result.sheet_meta,"warehouse","") or "")
    def card(self,surplus:Callable[[PipelineResult],int]|None=None)->dict:
        card:dict[str,Any]={"token":self.token,"name":self.result.output_name,"source":self.result.source_name,"warehouse":self.warehouse,"photos":len(self.photos)}
        if surplus is not None: card["surplus"]=surplus(self.result)
        return card
class JobStore:
    def __init__(self,settings)->None:self._settings=settings; self._lock=threading.Lock(); self._jobs:dict[str,Job]={}
    def get(self,token:str)->Job|None:return self._jobs.get(str(token or ""))
    def alive(self,token:str)->Job|None:
        job=self.get(token); return job if job is not None and job.alive else None
    def ready(self,token:str)->Job|None:
        job=self.alive(token); return job if job is not None and job.result.source_path.exists() else None
    def __contains__(self,token:object)->bool:return str(token or "") in self._jobs
    def __iter__(self)->Iterator[Job]:return iter(list(self._jobs.values()))
    def __len__(self)->int:return len(self._jobs)
    def cards(self,surplus:Callable[[PipelineResult],int]|None=None)->list[dict]:return [j.card(surplus) for j in self if j.alive]
    def results_view(self):return ResultsView(self)
    def remember(self,result:PipelineResult,strict:bool,verify:str,logic:int)->Job:
        job=Job(uuid.uuid4().hex,result,bool(strict),str(verify),int(logic))
        with self._lock:self._jobs[job.token]=job
        return job
    def replace(self,old_token:str,result:PipelineResult,strict:bool,verify:str,logic:int)->Job:
        old=self.get(old_token); job=self.remember(result,strict,verify,logic)
        if old is not None:
            job.photos=list(old.photos); job.photo_rows=set(old.photo_rows); job.mail_vision=dict(old.mail_vision); self.forget(old_token)
        return job
    def forget(self,token:str)->None:
        with self._lock:job=self._jobs.pop(str(token or ""),None)
        if job is not None:sweep(job.result.output_path.parent)
    def drop_expired(self)->int:
        edge=datetime.now()-timedelta(minutes=int(self._settings.result_ttl_minutes)); gone=0
        for job in self:
            if not job.alive or datetime.fromtimestamp(job.result.output_path.stat().st_mtime)<edge:self.forget(job.token); gone+=1
        return gone
    def as_dict(self)->dict[str,PipelineResult]:return {j.token:j.result for j in self if j.alive}
class ResultsView:
    def __init__(self,store:JobStore)->None:self._store=store
    def get(self,token:str,default=None):
        job=self._store.get(token); return job.result if job is not None and job.alive else default
    def __getitem__(self,token:str):
        result=self.get(token)
        if result is None:raise KeyError(token)
        return result
    def __contains__(self,token:object)->bool:return self._store.get(str(token or "")) is not None
    def __iter__(self):return iter(self._store.as_dict())
    def __len__(self):return len(self._store.as_dict())
    def pop(self,token:str,default=None):
        job=self._store.get(token)
        if job is None:return default
        result=job.result; self._store.forget(token); return result
