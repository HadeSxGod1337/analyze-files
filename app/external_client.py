import asyncio
import random
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from app.exceptions import FileUnavailable

NAMES_PATH = "/api/files/names"
DOWNLOAD_PATH = "/api/files/download"
DOWNLOADED_PATH = "/api/files/downloaded"

# Статусы вида "этого файла нет / имя невалидно": пофайловая ошибка, не процессная.
# Такой файл пропускаем и не роняем всё скачивание.
UNAVAILABLE_STATUSES = frozenset({404, 410, 422})

BASE_DELAY = 0.6
BACKOFF_FACTOR = 2.0
MAX_DELAY = 10.0
RECOVERY_FACTOR = 0.97
JITTER_RATIO = 0.25

OnWait = Callable[[float], None]


class FilesApiClient(Protocol):
    async def get_names(self) -> list[str]: ...
    async def download(self, names: list[str]) -> bytes: ...
    async def mark_downloaded(self, names: list[str]) -> None: ...


class ExternalClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        candidate_id: str,
        on_wait: OnWait | None = None,
    ) -> None:
        self._client = client
        self._headers = {"X-Candidate-Id": candidate_id}
        self.on_wait = on_wait
        self._delay = BASE_DELAY

    async def get_names(self) -> list[str]:
        response = await self._request("GET", NAMES_PATH)
        data: dict[str, Any] = response.json()
        return [str(name) for name in data["file_names"]]

    async def download(self, names: list[str]) -> bytes:
        try:
            response = await self._request("POST", DOWNLOAD_PATH, json={"file_names": names})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in UNAVAILABLE_STATUSES:
                raise FileUnavailable(", ".join(names)) from exc
            raise
        return response.content

    async def mark_downloaded(self, names: list[str]) -> None:
        await self._request("POST", DOWNLOADED_PATH, json={"file_names": names})

    async def _pace(self) -> None:
        await asyncio.sleep(self._delay + random.uniform(0, self._delay * JITTER_RATIO))

    def _back_off(self) -> None:
        self._delay = min(self._delay * BACKOFF_FACTOR, MAX_DELAY)

    def _speed_up(self) -> None:
        self._delay = max(self._delay * RECOVERY_FACTOR, BASE_DELAY)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        await self._pace()
        while True:
            response = await self._client.request(method, url, headers=self._headers, **kwargs)
            if response.status_code in (429, 403):
                retry_after = float(response.headers.get("Retry-After", "1"))
                if self.on_wait is not None:
                    self.on_wait(retry_after)
                await asyncio.sleep(retry_after)
                self._back_off()
                continue
            response.raise_for_status()
            self._speed_up()
            return response
