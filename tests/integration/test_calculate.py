import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import db
from app.routers import files as files_router


@dataclass
class Ctx:
    client: AsyncClient
    conn: aiosqlite.Connection
    files_dir: Path


@pytest_asyncio.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[Ctx]:
    conn = await db.connect(tmp_path / "app.db")
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    (files_dir / "a.txt").write_text("11223")
    (files_dir / "b.txt").write_text("999")

    await db.insert_file(conn, "a.txt", "2026-01-01T00:00:00+00:00", str(files_dir / "a.txt"))
    await db.insert_file(conn, "b.txt", "2026-01-02T00:00:00+00:00", str(files_dir / "b.txt"))

    app = FastAPI()
    app.state.conn = conn
    app.include_router(files_router.router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield Ctx(client=client, conn=conn, files_dir=files_dir)

    await conn.close()


async def _calc(ctx: Ctx, **body: object) -> dict[str, Any]:
    response = await ctx.client.post("/api/files/calculate", json=body)
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


@pytest.mark.asyncio
async def test_calculate_selected_files(ctx: Ctx) -> None:
    data = await _calc(ctx, names=["a.txt", "b.txt"])
    assert data["per_file"]["a.txt"]["1"] == 2
    assert data["per_file"]["a.txt"]["2"] == 2
    assert data["per_file"]["b.txt"]["9"] == 3
    assert data["total"]["9"] == 3
    assert data["total"]["1"] == 2


@pytest.mark.asyncio
async def test_calculate_only_one_file(ctx: Ctx) -> None:
    data = await _calc(ctx, names=["a.txt"])
    assert set(data["per_file"].keys()) == {"a.txt"}
    assert data["total"] == data["per_file"]["a.txt"]


@pytest.mark.asyncio
async def test_calculate_all(ctx: Ctx) -> None:
    data = await _calc(ctx, all=True)
    assert set(data["per_file"].keys()) == {"a.txt", "b.txt"}


@pytest.mark.asyncio
async def test_calculate_backfills_cache(ctx: Ctx) -> None:
    # Файлы вставлены без stats, первый расчёт должен досчитать и закешировать.
    await _calc(ctx, names=["a.txt"])

    rows = await db.get_stats(ctx.conn, ["a.txt"])
    assert rows[0].stats is not None
    assert json.loads(rows[0].stats)["counts"]["1"] == 2


@pytest.mark.asyncio
async def test_calculate_uses_cache_without_reading_files(ctx: Ctx) -> None:
    # Заполняем кеш, потом удаляем файл, расчёт берёт данные из кеша.
    await _calc(ctx, names=["a.txt"])
    (ctx.files_dir / "a.txt").unlink()

    data = await _calc(ctx, names=["a.txt"])
    assert data["per_file"]["a.txt"]["1"] == 2
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_recalculate_rereads_file(ctx: Ctx) -> None:
    # Кладём заведомо неверный кеш.
    bad = {"counts": dict.fromkeys("0123456789", 0), "non_digit": 0, "length": 500}
    await db.store_stats(ctx.conn, {"a.txt": json.dumps(bad)})

    stale = await _calc(ctx, names=["a.txt"])
    assert stale["per_file"]["a.txt"]["1"] == 0  # взяли неверный кеш

    fresh = await _calc(ctx, names=["a.txt"], recalculate=True)
    assert fresh["per_file"]["a.txt"]["1"] == 2  # перечитали файл


@pytest.mark.asyncio
async def test_calculate_reports_anomaly(ctx: Ctx) -> None:
    (ctx.files_dir / "c.txt").write_text("12ab")  # не-цифры, длина != 500
    await db.insert_file(
        ctx.conn, "c.txt", "2026-01-03T00:00:00+00:00", str(ctx.files_dir / "c.txt")
    )

    data = await _calc(ctx, names=["c.txt"])
    assert data["anomalies"][0]["name"] == "c.txt"
    assert data["anomalies"][0]["non_digit"] == 2


@pytest.mark.asyncio
async def test_missing_file_goes_to_errors_not_500(ctx: Ctx) -> None:
    await db.insert_file(ctx.conn, "gone.txt", "2026-01-04T00:00:00+00:00", "/no/such/file.txt")

    data = await _calc(ctx, names=["gone.txt"])
    assert data["errors"] == ["gone.txt"]
