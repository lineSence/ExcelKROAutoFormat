from __future__ import annotations
import threading
from ..core import mail_store
class LetterStore:
    def __init__(self,settings)->None:self._settings=settings; self._lock=threading.Lock(); self.items:list[dict]=[]; self.skipped=0
    def restore(self)->int:
        stored=mail_store.load(self._settings)
        with self._lock:self.items=list(stored)
        return len(self.items)
    def remember(self,fresh:list[dict])->int:
        with self._lock:self.items=mail_store.remember(self.items,list(fresh))
        return len(fresh)
    def note_skipped(self)->int:self.skipped=sum(1 for x in self.items if not x.get("photos")); return self.skipped
    def save(self)->None:mail_store.save(self._settings,self.items)
    def by_uid(self,uid:str)->dict|None:
        wanted=str(uid or ""); return next((x for x in self.items if str(x.get("uid"))==wanted),None)
    def forget_all(self)->None:
        mail_store.forget_all(self._settings)
        with self._lock:self.items=[]; self.skipped=0
