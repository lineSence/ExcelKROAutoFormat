from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse
from ..core import claims as claims_book,mail as mail_core,mail_vision,refs_sync,runtime,update as ota,vision as vision_core
from ..core.learning import dataset_stats
from ..core.verify import model_status
from ..deps import base,jobs,letters,logger,settings,templates
from ..services import letters as letters_service,photos as photos_service
from ..state.jobs import Job
from .forms import DEFAULT_LOGIC,mode,percent

def job_cards(with_surplus=False):
    return jobs.cards((lambda result:photos_service.surplus_count(result,base())) if with_surplus else None)
def only_job(token,cards):
    token=str(token or ""); return token if any(str(x["token"])==token for x in cards) else ""
def index_page(request:Request,error="",strict=None,verify=None,logic=None,status=200):
    current=base(); logic=int(round(float(getattr(current,"logic_weight",1.0))*100)) if logic is None else logic
    return templates.TemplateResponse(request=request,name="index.html",context={"error":error,"max_upload_mb":settings.max_upload_mb,"strict":settings.strict_resort if strict is None else bool(strict),"verify":mode(settings.verify_mode if verify is None else verify),"logic":percent(logic),"model":model_status(settings),"embed":runtime.embed_status(current),"judge":runtime.judge_status(current),"jobs":job_cards()},status_code=status)
def error_page(request,message,strict=False,verify="off",logic=DEFAULT_LOGIC,status=400):return index_page(request,error=message,strict=strict,verify=verify,logic=logic,status=status)
def result_page(request,job:Job,message=""):
    result=job.result; pending=[x for x in result.doubtful if not x.get("answered")]; link=letters_service.letters_for(result,letters); note=letters_service.mail_note(link["mine"]); report=job.mail_vision or {}
    if not message and note["letters"]:message=mail_vision.summary(report)
    elif not message and link["others"]:message=link["note"] or str(report.get("note") or "")
    return templates.TemplateResponse(request=request,name="result.html",context={"token":job.token,"summary":result.summary,"groups":result.groups,"clusters":result.clusters,"doubtful":pending,"confirmed_count":len(result.doubtful)-len(pending),"output_name":result.output_name,"strict":bool(job.strict),"verify":mode(job.verify),"logic":percent(job.logic),"refs":result.refs,"meta":result.sheet_meta.to_form(),"comparison":result.comparison,"claims":result.claims,"message":message,"photos":len(job.photos),"mail_note":note,"mail_letters":note["letters"],"mail_vision":report,"mail_others":len(link["others"])})
def vision_page(request,message="",error="",results=None,token="",status_code=200):
    current=base(); config=vision_core.load_config(current); cards=job_cards(True); token=only_job(token,cards); job=jobs.get(token); shown=results if results is not None else (job.photos if job else [])
    return templates.TemplateResponse(request=request,name="vision.html",context={"vision":vision_core.status(current,config),"config":config,"vision_max_photo_mb":config["vision_max_photo_mb"],"jobs":cards,"token":token,"results":shown,"message":message,"error":error},status_code=status_code)
def mail_page(request,message="",error="",status_code=200,token=""):
    cards=job_cards(); return templates.TemplateResponse(request=request,name="mail.html",context={"mail":mail_core.status(settings),"letters":letters.items,"rows":letters_service.mail_list(letters),"jobs":cards,"token":only_job(token,cards),"skipped":letters.skipped,"message":message,"error":error},status_code=status_code)
def refs_page(request,note="",error=""):
    state=refs_sync.load_state(settings.refs_state_path); return templates.TemplateResponse(request=request,name="refs.html",context={"state":state,"books":refs_sync.book_status(settings.refs_dir),"claims":claims_book.status(settings.refs_dir),"checker":settings.default_checker,"cells":settings.refs_cells(),"local_dir":settings.refs_dir,"max_upload_mb":settings.max_upload_mb,"fill_log":state.fill_log,"min_score":settings.refs_match_min_score,"days_around":settings.refs_days_around,"note":note,"error":error})
def update_page(request,message="",error="",status_code=200):
    info=ota.status(); return templates.TemplateResponse(request=request,name="update.html",context={"status":info,"state":info["state"],"message":message,"error":error},status_code=status_code)
def training_page(request,message="",error="",report=None,status_code=200):
    current=base(); return templates.TemplateResponse(request=request,name="training.html",context={"model":model_status(settings),"embed":runtime.embed_status(current),"judge":runtime.judge_status(current),"stats":dataset_stats(settings.train_store_path),"message":message,"error":error,"report":report,"max_upload_mb":settings.max_upload_mb},status_code=status_code)
