"""Entry point: the UI and API on one port, the VLM proxy on another."""

import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, api, config, llm, runner, s3store, storage
from .vlm_proxy import proxy_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("parser-eval")

app = FastAPI(title="parser-eval", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(api.public)
app.include_router(api.api)


@app.exception_handler(s3store.S3Error)
async def s3_error(_: Request, exc: s3store.S3Error):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(llm.LLMError)
async def llm_error(_: Request, exc: llm.LLMError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": __version__}


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def _housekeeping() -> None:
    while True:
        try:
            removed = runner.cleanup_old_runs()
            if removed:
                log.info("removed %d old runs", removed)
        except Exception:
            log.exception("cleanup failed")
        await asyncio.sleep(6 * 3600)


@app.on_event("startup")
async def startup() -> None:
    storage.ensure_dirs()
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not config.APP_SECRET_KEY:
        log.warning("APP_SECRET_KEY is not set. Use this only for local development.")
    interrupted = runner.recover_on_start()
    if interrupted:
        log.info("marked %d runs as interrupted: %s", len(interrupted), ", ".join(interrupted))
    asyncio.get_running_loop().create_task(runner.queue.worker())
    asyncio.get_running_loop().create_task(_housekeeping())


class _QuietServer(uvicorn.Server):
    """The proxy server leaves signal handling to the UI server and stops with it."""

    def capture_signals(self):
        return contextlib.nullcontext()


async def serve() -> None:
    ui = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.UI_PORT, log_level="info",
                                       proxy_headers=True, forwarded_allow_ips="*"))
    proxy = _QuietServer(uvicorn.Config(proxy_app, host="0.0.0.0", port=config.PROXY_PORT, log_level="warning"))
    proxy_task = asyncio.create_task(proxy.serve())
    try:
        await ui.serve()
    finally:
        proxy.should_exit = True
        await proxy_task


if __name__ == "__main__":
    asyncio.run(serve())
