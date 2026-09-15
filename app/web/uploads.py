from __future__ import annotations
from pathlib import Path
from fastapi import UploadFile
from ..core.guard import UploadTooLarge
CHUNK=1024*1024
async def save_upload(file:UploadFile,target:Path,limit_mb:int)->int:
    limit=max(0,int(limit_mb))*1024*1024; target.parent.mkdir(parents=True,exist_ok=True); size=0
    try:
        with target.open("wb") as handle:
            while True:
                chunk=await file.read(CHUNK)
                if not chunk: break
                size+=len(chunk)
                if size>limit: raise UploadTooLarge(f"Файл больше {limit_mb} МБ.")
                handle.write(chunk)
    except UploadTooLarge:
        target.unlink(missing_ok=True); raise
    finally: await file.close()
    return size
