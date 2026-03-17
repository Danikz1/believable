"""Believable Minds — FastAPI Application."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.admin import router as admin_router
from src.api.public import router as public_router
from src.logging_config import setup_logging

# Initialize structured logging before anything else
setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background scheduler on startup, stop on shutdown."""
    from src.pipeline.scheduler import start_scheduler, stop_scheduler
    logger.info("Starting background scheduler...")
    start_scheduler()
    yield
    logger.info("Stopping background scheduler...")
    stop_scheduler()


app = FastAPI(
    title="Believable Minds API",
    description="Track what credible minds say — structured claims from podcast transcripts.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public_router, prefix="/api", tags=["Public"])
app.include_router(admin_router, prefix="/admin", tags=["Admin"])

# Static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
def health():
    from src.pipeline.scheduler import get_scheduler_status
    return {"status": "ok", "scheduler": get_scheduler_status()}

