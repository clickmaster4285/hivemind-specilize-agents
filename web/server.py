"""FastAPI gateway: assembles the per-domain routers onto one app.

Route handlers live in ``web/routers/*`` and request models in ``web/schemas.py``; this module only
wires the app together — startup (DB, model sync, worker, schedulers, warm-ups), CORS, the routers,
and the static frontend. Add a new area of the API as a new router module, not more code here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core.auth import bootstrap_admin
from src.core.config import Config
from src.core.db import init_db
from src.jobs.worker import start_inprocess_worker
from src.llm.model_registry import sync_models_from_ollama
from web.brain_routes import router as brain_router
from web.routers import auth, history, jobs, models, servers, specialists, system, users

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from src.core.db import session_scope

    config = Config.from_env()
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.artifacts_dir.mkdir(parents=True, exist_ok=True)
    init_db(config)
    bootstrap_admin(config)
    with session_scope(config) as session:
        sync_models_from_ollama(session, config, probe_details=True)
    start_inprocess_worker(config)
    if config.llm_warm_on_start:
        # Preload the chat model so the first message isn't a cold load (own thread; never blocks startup).
        import threading

        from src.llm.ollama_mgmt import warm_model

        threading.Thread(target=warm_model, args=(config,), name="chat-model-warm", daemon=True).start()
    # Keep the knowledge brain fresh: re-check sources whose refresh_every has passed
    from src.brain.scheduler import start_scheduler

    start_scheduler(config)
    if config.image_warm_on_start:
        # SDXL lives in its own process; start it (and load the model) in the background
        import threading

        from src.images.image_client import ImageServiceClient

        threading.Thread(target=ImageServiceClient(config).warm, name="image-service-warm", daemon=True).start()
    yield


app = FastAPI(title="MulteAgent API", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# One router per domain (web/routers/*). brain_router carries its own /api/brain prefix.
for module in (system, auth, users, specialists, servers, models, jobs, history):
    app.include_router(module.router)
app.include_router(brain_router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    # Show our own INFO logs (e.g. SSH connect timing) in the console, not just uvicorn's.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    uvicorn.run("web.server:app", host=config.api_host, port=config.api_port, reload=False)


if __name__ == "__main__":
    main()
