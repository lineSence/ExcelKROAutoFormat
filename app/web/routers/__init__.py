from . import companion, health, mail, refs, result, training, update, upload, vision
ROUTERS=(health.router,upload.router,result.router,vision.router,mail.router,refs.router,update.router,training.router,companion.router)
__all__=["ROUTERS"]
