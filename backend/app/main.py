"""FastAPI entrypoint — initializes SQLite, loads persisted settings, starts ScannerEngine,
and serves built frontend SPA in production deployment."""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.db import init_db, load_kv
from app.scanner.engine import ScannerEngine
from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

engine = ScannerEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    saved = await load_kv("settings")
    if saved:
        th = saved.pop("thresholds", None)
        for k, v in saved.items():
            if hasattr(settings, k):
                setattr(settings, k, v)
        if th:
            for k, v in th.items():
                if hasattr(settings.thresholds, k):
                    setattr(settings.thresholds, k, int(v))
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Binance Crypto Intelligence Scanner", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)

DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
if os.path.isdir(DIST_DIR):
    assets_dir = os.path.join(DIST_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        candidate = os.path.join(DIST_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST_DIR, "index.html"))
