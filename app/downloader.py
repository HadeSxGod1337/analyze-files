import asyncio
import contextlib
import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import aiosqlite
import anyio

from app import db, digits
from app.exceptions import FileUnavailable
from app.external_client import FilesApiClient

DOWNLOAD_CHUNK_SIZE = 3


class DownloadState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class DownloadStatus:
    state: DownloadState = DownloadState.IDLE
    started_at: str | None = None
    finished_at: str | None = None
    last_batch_at: str | None = None
    names_seen: int = 0
    downloaded: int = 0
    in_catalog: int = 0
    failed: list[str] = field(default_factory=list)
    blocked_until: str | None = None
    error: str | None = None


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def extract_zip(data: bytes, target_dir: Path) -> dict[str, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    contents: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.namelist():
            name = Path(entry).name
            raw = archive.read(entry)
            (target_dir / name).write_bytes(raw)
            contents[name] = raw.decode(errors="replace")
    return contents


class Downloader:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        client: FilesApiClient,
        files_dir: Path,
        precompute: bool = True,
    ) -> None:
        self._conn = conn
        self._client = client
        self._files_dir = files_dir
        self._precompute = precompute
        self.status = DownloadStatus()
        self._failed: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> DownloadStatus:
        if self.status.state != DownloadState.RUNNING:
            self._failed = set()
            self.status = DownloadStatus(
                state=DownloadState.RUNNING,
                started_at=datetime.now(UTC).isoformat(),
                in_catalog=self.status.in_catalog,
            )
            self._task = asyncio.create_task(self._run())
        return self.status

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def load_catalog_count(self) -> None:
        self.status.in_catalog = await db.count_files(self._conn)

    def on_wait(self, retry_after: float) -> None:
        until = datetime.now(UTC) + timedelta(seconds=retry_after)
        self.status.blocked_until = until.isoformat()

    async def _run(self) -> None:
        self.status.in_catalog = await db.count_files(self._conn)
        try:
            while True:
                names = await self._client.get_names()
                self.status.blocked_until = None
                # Провальные имена сервер отдаёт снова и снова (мы их не отмечаем);
                # каталог пройден, когда новых имён не осталось.
                new = [name for name in names if name not in self._failed]
                if not new:
                    break
                self.status.names_seen += len(new)
                await self._download_batch(new)
            self.status.state = DownloadState.DONE
        except Exception as exc:
            self.status.state = DownloadState.ERROR
            self.status.error = str(exc)
        finally:
            self.status.finished_at = datetime.now(UTC).isoformat()

    async def _download_batch(self, names: list[str]) -> None:
        saved: list[str] = []
        for chunk in chunks(names, DOWNLOAD_CHUNK_SIZE):
            saved.extend(await self._download_chunk(chunk))
        # Отмечаем скачанное одним запросом только после реальной записи на диск -
        # меньше обращений к API и, значит, меньше поводов словить лимит.
        if saved:
            await self._client.mark_downloaded(saved)
            self.status.blocked_until = None
            self.status.last_batch_at = datetime.now(UTC).isoformat()

    async def _download_chunk(self, names: list[str]) -> list[str]:
        try:
            contents = await self._fetch(names)
        except (FileUnavailable, zipfile.BadZipFile):
            # Чанк не скачался целиком - изолируем виновника, качая по одному.
            return await self._download_individually(names)
        return await self._save(names, contents)

    async def _download_individually(self, names: list[str]) -> list[str]:
        saved: list[str] = []
        for name in names:
            try:
                contents = await self._fetch([name])
            except (FileUnavailable, zipfile.BadZipFile):
                self._failed.add(name)
                self.status.failed = sorted(self._failed)
                continue
            saved.extend(await self._save([name], contents))
        return saved

    async def _fetch(self, names: list[str]) -> dict[str, str]:
        archive = await self._client.download(names)
        self.status.blocked_until = None
        return await anyio.to_thread.run_sync(extract_zip, archive, self._files_dir)

    async def _save(self, names: list[str], contents: dict[str, str]) -> list[str]:
        downloaded_at = datetime.now(UTC).isoformat()
        for name in names:
            stats = None
            if self._precompute and name in contents:
                stats = json.dumps(digits.analyze(contents[name]))
            path = str(self._files_dir / name)
            await db.insert_file(self._conn, name, downloaded_at, path, stats)
            self.status.downloaded += 1
            self.status.in_catalog += 1
        return names
