from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch

from app.exceptions import FileUnavailable
from app.external_client import BASE_DELAY, JITTER_RATIO, MAX_DELAY, ExternalClient

Handler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


def _sequence_handler(responses: list[httpx.Response]) -> Handler:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    return handler


@pytest.fixture
def sleep_calls(monkeypatch: MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("app.external_client.asyncio.sleep", fake_sleep)
    # Убираем случайность джиттера, чтобы паузы были детерминированы.
    monkeypatch.setattr("app.external_client.random.uniform", lambda a, b: 0.0)
    return recorded


@pytest.mark.asyncio
async def test_paces_every_request_by_base_delay(sleep_calls: list[float]) -> None:
    handler = _sequence_handler([httpx.Response(200, json={"file_names": []})])
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    await client.get_names()
    await client.get_names()
    await client.get_names()

    # Постоянный пол паузы перед каждым запросом, никакого стартового залпа.
    assert sleep_calls == [BASE_DELAY, BASE_DELAY, BASE_DELAY]

    await http_client.aclose()


@pytest.mark.asyncio
async def test_honors_retry_after_on_429_and_backs_off(sleep_calls: list[float]) -> None:
    handler = _sequence_handler(
        [
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"file_names": ["a.txt"]}),
        ]
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")

    on_wait_calls: list[float] = []
    client = ExternalClient(http_client, "cand-1", on_wait=on_wait_calls.append)

    assert await client.get_names() == ["a.txt"]
    assert on_wait_calls == [2.0]
    # Пол-пауза перед запросом, затем честное ожидание Retry-After.
    assert sleep_calls == [BASE_DELAY, 2.0]
    # После 429 темп вырос, но остался в пределах потолка.
    assert BASE_DELAY < client._delay <= MAX_DELAY

    await http_client.aclose()


@pytest.mark.asyncio
async def test_403_ban_does_not_become_the_pace(sleep_calls: list[float]) -> None:
    handler = _sequence_handler(
        [
            httpx.Response(403, headers={"Retry-After": "1800"}),
            httpx.Response(200, json={"file_names": []}),
            httpx.Response(200, json={"file_names": []}),
        ]
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    await client.get_names()
    assert 1800.0 in sleep_calls  # бан пережидаем честно

    # Ключевая регрессия: 1800 не стал межзапросным темпом.
    await client.get_names()
    assert sleep_calls[-1] <= MAX_DELAY
    assert client._delay <= MAX_DELAY

    await http_client.aclose()


@pytest.mark.asyncio
async def test_recovers_slowly_down_to_base_delay(sleep_calls: list[float]) -> None:
    responses = [httpx.Response(429, headers={"Retry-After": "1"})] + [
        httpx.Response(200, json={"file_names": []}) for _ in range(60)
    ]
    handler = _sequence_handler(responses)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    for _ in range(60):
        await client.get_names()

    # Долгая серия успехов возвращает темп к полу, но не ниже.
    assert client._delay == pytest.approx(BASE_DELAY)
    assert sleep_calls[-1] == pytest.approx(BASE_DELAY)

    await http_client.aclose()


@pytest.mark.asyncio
async def test_jitter_added_to_delay(monkeypatch: MonkeyPatch) -> None:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("app.external_client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.external_client.random.uniform", lambda a, b: b)  # максимум джиттера

    handler = _sequence_handler([httpx.Response(200, json={"file_names": []})])
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    await client.get_names()

    assert recorded[-1] == pytest.approx(BASE_DELAY + BASE_DELAY * JITTER_RATIO)

    await http_client.aclose()


@pytest.mark.asyncio
async def test_download_sends_candidate_header(sleep_calls: list[float]) -> None:
    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, content=b"zip-bytes")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-42")

    content = await client.download(["a.txt"])

    assert content == b"zip-bytes"
    assert seen_headers["x-candidate-id"] == "cand-42"

    await http_client.aclose()


@pytest.mark.asyncio
async def test_download_404_becomes_file_unavailable(sleep_calls: list[float]) -> None:
    handler = _sequence_handler([httpx.Response(404)])
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    with pytest.raises(FileUnavailable):
        await client.download(["gone.txt"])

    await http_client.aclose()


@pytest.mark.asyncio
async def test_download_500_propagates(sleep_calls: list[float]) -> None:
    handler = _sequence_handler([httpx.Response(500)])
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    client = ExternalClient(http_client, "cand-1")

    # 5xx - процессная ошибка, не пофайловая: пробрасываем как HTTPStatusError.
    with pytest.raises(httpx.HTTPStatusError):
        await client.download(["a.txt"])

    await http_client.aclose()
