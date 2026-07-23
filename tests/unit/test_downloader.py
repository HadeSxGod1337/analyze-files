import io
import json
import zipfile
from pathlib import Path

import pytest

from app import db
from app.downloader import Downloader, DownloadState, chunks, extract_zip
from app.exceptions import FileUnavailable


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, []),
        (1, [["a"]]),
        (3, [["a", "b", "c"]]),
        (4, [["a", "b", "c"], ["d"]]),
        (7, [["a", "b", "c"], ["d", "e", "f"], ["g"]]),
    ],
)
def test_chunks_boundaries(count: int, expected: list[list[str]]) -> None:
    items = ["a", "b", "c", "d", "e", "f", "g"][:count]
    assert list(chunks(items, 3)) == expected


def test_extract_zip(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("a.txt", "123")

    target = tmp_path / "files"
    contents = extract_zip(buf.getvalue(), target)

    assert contents == {"a.txt": "123"}
    assert (target / "a.txt").read_text() == "123"


class FakeClient:
    def __init__(
        self,
        batches: list[list[str]],
        unavailable: set[str] | None = None,
        corrupt: set[str] | None = None,
    ) -> None:
        self._batches = iter(batches)
        self._unavailable = unavailable or set()
        self._corrupt = corrupt or set()
        self.downloaded_calls: list[list[str]] = []
        self.marked_calls: list[list[str]] = []

    async def get_names(self) -> list[str]:
        return next(self._batches, [])

    async def download(self, names: list[str]) -> bytes:
        self.downloaded_calls.append(names)
        if any(name in self._unavailable for name in names):
            raise FileUnavailable(", ".join(names))
        if any(name in self._corrupt for name in names):
            return b"this is not a zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            for name in names:
                archive.writestr(name, "0" * 500)
        return buf.getvalue()

    async def mark_downloaded(self, names: list[str]) -> None:
        self.marked_calls.append(names)


@pytest.mark.asyncio
async def test_downloader_full_cycle(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([["a.txt", "b.txt", "c.txt", "d.txt"], []])
    downloader = Downloader(conn, client, tmp_path / "files")

    status = downloader.start()
    assert status.state == DownloadState.RUNNING
    assert downloader._task is not None
    await downloader._task

    assert downloader.status.state == DownloadState.DONE
    assert downloader.status.names_seen == 4
    assert downloader.status.downloaded == 4
    assert client.downloaded_calls == [["a.txt", "b.txt", "c.txt"], ["d.txt"]]
    # Отметка одним запросом на весь батч, не по чанкам.
    assert client.marked_calls == [["a.txt", "b.txt", "c.txt", "d.txt"]]
    assert await db.count_files(conn) == 4

    # precompute по умолчанию включён, статистика посчитана при скачивании.
    rows = {r.name: r for r in await db.get_all_stats(conn)}
    assert rows["a.txt"].stats is not None
    assert json.loads(rows["a.txt"].stats)["counts"]["0"] == 500

    # Тайминги проставлены, счётчик каталога отражает скачанное.
    assert downloader.status.started_at is not None
    assert downloader.status.finished_at is not None
    assert downloader.status.last_batch_at is not None
    assert downloader.status.in_catalog == 4

    await conn.close()


@pytest.mark.asyncio
async def test_empty_catalog_finishes_without_batch(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([[]])
    downloader = Downloader(conn, client, tmp_path / "files")

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    assert downloader.status.state == DownloadState.DONE
    assert downloader.status.finished_at is not None
    assert downloader.status.last_batch_at is None

    await conn.close()


@pytest.mark.asyncio
async def test_downloader_precompute_off_leaves_stats_null(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([["a.txt"], []])
    downloader = Downloader(conn, client, tmp_path / "files", precompute=False)

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    rows = await db.get_all_stats(conn)
    assert rows[0].stats is None

    await conn.close()


@pytest.mark.asyncio
async def test_downloader_start_is_idempotent_while_running(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([[]])
    downloader = Downloader(conn, client, tmp_path / "files")

    first = downloader.start()
    second = downloader.start()
    assert first is second

    assert downloader._task is not None
    await downloader._task
    await conn.close()


@pytest.mark.asyncio
async def test_unavailable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([["a.txt", "b.txt", "c.txt"], []], unavailable={"b.txt"})
    downloader = Downloader(conn, client, tmp_path / "files")

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    # Плохой файл пропущен, соседи сохранены, процесс не упал.
    assert downloader.status.state == DownloadState.DONE
    assert downloader.status.failed == ["b.txt"]
    assert downloader.status.downloaded == 2
    assert await db.count_files(conn) == 2
    # b.txt не отмечали на сервере.
    marked = [name for call in client.marked_calls for name in call]
    assert "b.txt" not in marked

    await conn.close()


@pytest.mark.asyncio
async def test_corrupt_zip_is_skipped(tmp_path: Path) -> None:
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([["a.txt", "b.txt"], []], corrupt={"b.txt"})
    downloader = Downloader(conn, client, tmp_path / "files")

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    assert downloader.status.state == DownloadState.DONE
    assert downloader.status.failed == ["b.txt"]
    assert await db.count_files(conn) == 1

    await conn.close()


@pytest.mark.asyncio
async def test_failed_name_does_not_loop_forever(tmp_path: Path) -> None:
    # Сервер отдаёт нескачиваемое имя снова: цикл должен завершиться, и не зациклиться.
    conn = await db.connect(tmp_path / "app.db")
    client = FakeClient([["bad.txt"], ["bad.txt"], ["bad.txt"]], unavailable={"bad.txt"})
    downloader = Downloader(conn, client, tmp_path / "files")

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    assert downloader.status.state == DownloadState.DONE
    assert downloader.status.failed == ["bad.txt"]
    assert await db.count_files(conn) == 0

    await conn.close()


@pytest.mark.asyncio
async def test_non_file_error_is_fatal(tmp_path: Path) -> None:
    # Сетевая, не пофайловая ошибка должна ронять процесс в состояние ошибки, не глотаться.
    conn = await db.connect(tmp_path / "app.db")

    class BrokenClient(FakeClient):
        async def download(self, names: list[str]) -> bytes:
            raise ConnectionError("network down")

    client = BrokenClient([["a.txt"], []])
    downloader = Downloader(conn, client, tmp_path / "files")

    downloader.start()
    assert downloader._task is not None
    await downloader._task

    assert downloader.status.state == DownloadState.ERROR
    assert downloader.status.error is not None

    await conn.close()
