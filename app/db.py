import uuid
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    name TEXT PRIMARY KEY,
    downloaded_at TEXT NOT NULL,
    path TEXT NOT NULL,
    stats TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# SQLite ограничивает число host-параметров в запросе (по умолчанию 999); режем
# длинные IN-списки на куски, чтобы выборка не падала на больших наборах имён.
IN_CHUNK = 900


@dataclass(frozen=True)
class FileRecord:
    name: str
    downloaded_at: str


@dataclass(frozen=True)
class FileStatsRow:
    name: str
    path: str
    stats: str | None


async def connect(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    await conn.executescript(SCHEMA)
    await _ensure_stats_column(conn)
    await conn.commit()
    return conn


async def _ensure_stats_column(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA table_info(files)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if "stats" not in columns:
        await conn.execute("ALTER TABLE files ADD COLUMN stats TEXT")


async def get_or_create_candidate_id(conn: aiosqlite.Connection) -> str:
    async with conn.execute("SELECT value FROM meta WHERE key = 'candidate_id'") as cur:
        row = await cur.fetchone()
    if row is not None:
        return str(row[0])

    candidate_id = str(uuid.uuid4())
    await conn.execute("INSERT INTO meta (key, value) VALUES ('candidate_id', ?)", (candidate_id,))
    await conn.commit()
    return candidate_id


async def insert_file(
    conn: aiosqlite.Connection,
    name: str,
    downloaded_at: str,
    path: str,
    stats: str | None = None,
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO files (name, downloaded_at, path, stats) VALUES (?, ?, ?, ?)",
        (name, downloaded_at, path, stats),
    )
    await conn.commit()


async def count_files(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT COUNT(*) FROM files") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_files(
    conn: aiosqlite.Connection, page: int, page_size: int, descending: bool
) -> list[FileRecord]:
    order = "DESC" if descending else "ASC"
    offset = (page - 1) * page_size
    query = f"SELECT name, downloaded_at FROM files ORDER BY downloaded_at {order} LIMIT ? OFFSET ?"
    async with conn.execute(query, (page_size, offset)) as cur:
        rows = await cur.fetchall()
    return [FileRecord(name=row[0], downloaded_at=row[1]) for row in rows]


async def get_all_stats(conn: aiosqlite.Connection) -> list[FileStatsRow]:
    async with conn.execute("SELECT name, path, stats FROM files") as cur:
        rows = await cur.fetchall()
    return [FileStatsRow(name=row[0], path=row[1], stats=row[2]) for row in rows]


async def get_stats(conn: aiosqlite.Connection, names: list[str]) -> list[FileStatsRow]:
    result: list[FileStatsRow] = []
    for start in range(0, len(names), IN_CHUNK):
        chunk = names[start : start + IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        query = f"SELECT name, path, stats FROM files WHERE name IN ({placeholders})"
        async with conn.execute(query, chunk) as cur:
            rows = await cur.fetchall()
        result.extend(FileStatsRow(name=row[0], path=row[1], stats=row[2]) for row in rows)
    return result


async def store_stats(conn: aiosqlite.Connection, stats_by_name: dict[str, str]) -> None:
    if not stats_by_name:
        return
    await conn.executemany(
        "UPDATE files SET stats = ? WHERE name = ?",
        [(stats, name) for name, stats in stats_by_name.items()],
    )
    await conn.commit()
