from pathlib import Path

import pytest

from app import db


@pytest.mark.asyncio
async def test_pagination_and_sort(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    for i in range(5):
        await db.insert_file(conn, f"f{i}.txt", f"2026-01-0{i + 1}T00:00:00+00:00", "x")

    ascending = await db.list_files(conn, page=1, page_size=2, descending=False)
    assert [r.name for r in ascending] == ["f0.txt", "f1.txt"]

    descending = await db.list_files(conn, page=1, page_size=2, descending=True)
    assert [r.name for r in descending] == ["f4.txt", "f3.txt"]

    second_page = await db.list_files(conn, page=2, page_size=2, descending=False)
    assert [r.name for r in second_page] == ["f2.txt", "f3.txt"]

    assert await db.count_files(conn) == 5

    await conn.close()


@pytest.mark.asyncio
async def test_candidate_id_persists(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = await db.connect(db_path)
    candidate_id = await db.get_or_create_candidate_id(conn)
    await conn.close()

    conn2 = await db.connect(db_path)
    assert await db.get_or_create_candidate_id(conn2) == candidate_id
    await conn2.close()


@pytest.mark.asyncio
async def test_stats_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = await db.connect(db_path)
    await db.insert_file(conn, "a.txt", "2026-01-01T00:00:00+00:00", "/data/a.txt")
    await conn.close()

    # Повторный connect на существующей БД не должен падать на ALTER.
    conn2 = await db.connect(db_path)
    rows = await db.get_stats(conn2, ["a.txt"])
    assert rows[0].name == "a.txt"
    assert rows[0].stats is None
    await conn2.close()


@pytest.mark.asyncio
async def test_store_and_get_stats(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    await db.insert_file(conn, "a.txt", "2026-01-01T00:00:00+00:00", "/data/a.txt")
    await db.insert_file(conn, "b.txt", "2026-01-01T00:00:00+00:00", "/data/b.txt", stats='{"x":1}')

    await db.store_stats(conn, {"a.txt": '{"y":2}'})

    by_name = {r.name: r for r in await db.get_stats(conn, ["a.txt", "b.txt"])}
    assert by_name["a.txt"].stats == '{"y":2}'
    assert by_name["a.txt"].path == "/data/a.txt"
    assert by_name["b.txt"].stats == '{"x":1}'
    assert {r.name for r in await db.get_all_stats(conn)} == {"a.txt", "b.txt"}

    await conn.close()


@pytest.mark.asyncio
async def test_get_stats_chunks_large_name_list(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    names = [f"f{i}.txt" for i in range(2000)]
    for name in names:
        await db.insert_file(conn, name, "2026-01-01T00:00:00+00:00", f"/data/{name}")

    # Более 999 имён должно пройти без ошибки лимита параметров SQLite.
    rows = await db.get_stats(conn, names)
    assert len(rows) == 2000

    await conn.close()
