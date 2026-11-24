from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routers import analysis, artwork, chapters, dashboard, jobs, library, logs, settings, system, templates
from backend.infrastructure.config import load_config
from backend.infrastructure.database import init_db
from backend.infrastructure.logging_config import configure_logging
from backend.services.job_service import job_service
from backend.services.plugin_manager import start_background_analysis_setup

ROOT = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    configure_logging()
    init_db()
    # Keep the GUI responsive while the first-run FFmpeg/FFprobe setup runs.
    # Analysis jobs acquire the plugin manager lock and wait for this setup.
    plugin_state = start_background_analysis_setup(cfg)
    logging.getLogger(__name__).info("Background multimedia tool setup started: %s", plugin_state.get("status"))
    job_service.recover_interrupted_jobs()
    logging.getLogger(__name__).info("OpEdTagger backend started")
    yield
    logging.getLogger(__name__).info("OpEdTagger backend stopped")


app = FastAPI(
    title="OpEdTagger API",
    version="1.5.2",
    description="Local API for anime OP/ED analysis and MKV chapter management.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

for route_module in (system, dashboard, library, jobs, templates, analysis, chapters, logs, settings, artwork):
    app.include_router(route_module.router, prefix="/api")


@app.get("/api/health")
def health_alias():
    return {"status": "ok", "service": "OpEdTagger"}


# Production build is served by FastAPI. During development Vite proxies /api to port 8000.
frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
