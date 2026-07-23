import json
from pathlib import Path

import anyio
from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import db, digits
from app.db import FileStatsRow
from app.digits import FileStats

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

PAGE_SIZE = 20


@router.get("/files")
async def files_page(request: Request, page: int = 1, sort: str = "desc") -> Response:
    conn = request.app.state.conn
    total = await db.count_files(conn)
    records = await db.list_files(conn, page=page, page_size=PAGE_SIZE, descending=sort != "asc")
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "files.html",
        {"files": records, "page": page, "total_pages": total_pages, "sort": sort},
    )


class CalculateRequest(BaseModel):
    names: list[str] = []
    all: bool = False
    recalculate: bool = False


class Anomaly(BaseModel):
    name: str
    non_digit: int
    length: int


class CalculateResponse(BaseModel):
    per_file: dict[str, dict[str, int]]
    total: dict[str, int]
    anomalies: list[Anomaly]
    errors: list[str]


# Сбой чтения отдельного файла не должен ронять весь расчёт, поэтому копим ошибки.
def _analyze_from_disk(rows: list[FileStatsRow]) -> tuple[dict[str, FileStats], list[str]]:
    analyzed: dict[str, FileStats] = {}
    errors: list[str] = []
    for row in rows:
        try:
            analyzed[row.name] = digits.analyze(Path(row.path).read_text(errors="replace"))
        except OSError:
            errors.append(row.name)
    return analyzed, errors


@router.post("/api/files/calculate", response_model=CalculateResponse)
async def calculate(request: Request, body: CalculateRequest) -> CalculateResponse:
    conn = request.app.state.conn
    rows = await db.get_all_stats(conn) if body.all else await db.get_stats(conn, body.names)

    analyzed: dict[str, FileStats] = {}
    to_read = rows
    if not body.recalculate:
        # Готовые из кеша берём как есть, читаем только промахи.
        to_read = []
        for row in rows:
            if row.stats is not None:
                analyzed[row.name] = json.loads(row.stats)
            else:
                to_read.append(row)

    read_analyzed, errors = await anyio.to_thread.run_sync(_analyze_from_disk, to_read)
    analyzed.update(read_analyzed)

    # Досчитанное/пересчитанное складываем в кеш, чтобы следующий расчёт был мгновенным
    if read_analyzed:
        await db.store_stats(conn, {name: json.dumps(a) for name, a in read_analyzed.items()})

    per_file = {name: a["counts"] for name, a in analyzed.items()}
    total = digits.sum_digit_counts(list(per_file.values()))
    anomalies = [
        Anomaly(name=name, non_digit=a["non_digit"], length=a["length"])
        for name, a in analyzed.items()
        if a["non_digit"] or a["length"] != digits.EXPECTED_LENGTH
    ]
    return CalculateResponse(per_file=per_file, total=total, anomalies=anomalies, errors=errors)
