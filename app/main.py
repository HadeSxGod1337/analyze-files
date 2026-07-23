from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import get_settings
from app.downloader import Downloader
from app.external_client import ExternalClient
from app.routers import download, files

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    conn = await db.connect(settings.db_path)
    candidate_id = await db.get_or_create_candidate_id(conn)

    http_client = httpx.AsyncClient(base_url=settings.external_base_url, timeout=30.0)
    client = ExternalClient(http_client, candidate_id)
    downloader = Downloader(
        conn, client, settings.files_dir, precompute=settings.precompute_on_download
    )
    client.on_wait = downloader.on_wait
    await downloader.load_catalog_count()

    app.state.conn = conn
    app.state.downloader = downloader

    try:
        yield
    finally:
        await downloader.stop()
        await http_client.aclose()
        await conn.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(download.router)
app.include_router(files.router)
