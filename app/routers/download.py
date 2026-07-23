from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.downloader import Downloader, DownloadStatus

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse(url="/download")


@router.get("/download")
async def download_page(request: Request) -> Response:
    downloader: Downloader = request.app.state.downloader
    return templates.TemplateResponse(request, "download.html", {"status": downloader.status})


@router.post("/api/download/start")
async def start_download(request: Request) -> DownloadStatus:
    downloader: Downloader = request.app.state.downloader
    return downloader.start()


@router.get("/api/download/status")
async def get_status(request: Request) -> DownloadStatus:
    downloader: Downloader = request.app.state.downloader
    return downloader.status
