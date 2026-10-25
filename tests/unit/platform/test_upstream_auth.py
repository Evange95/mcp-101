"""Service access tokens: providers, BearerAuth, and that the secret never leaks."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from pokedex_mcp.platform.telemetry.log import LoggingTokenProbe
from pokedex_mcp.platform.upstream.auth import (
    BearerAuth,
    FileTokenProvider,
    StaticTokenProvider,
    TokenUnavailable,
)

pytestmark = pytest.mark.anyio

BASE_URL = "https://digimon.test/api"
SECRET = "s3cr3t-token-v1"
ROTATED = "s3cr3t-token-v2"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingTokenProbe:
    def __init__(self) -> None:
        self.events: list[str] = []

    def token_loaded(self, upstream: str) -> None:
        self.events.append(f"loaded {upstream}")

    def token_rejected(self, upstream: str, *, retried: bool) -> None:
        self.events.append(f"rejected {upstream} retried={retried}")

    def token_unavailable(self, upstream: str, reason: str) -> None:
        self.events.append(f"unavailable {upstream}: {reason}")


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(f"{SECRET}\n", encoding="utf-8")  # Secrets often end with a newline
    return path


def rotate(path: Path, token: str) -> None:
    path.write_text(f"{token}\n", encoding="utf-8")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def probe() -> RecordingTokenProbe:
    return RecordingTokenProbe()


@pytest.fixture
def provider(token_file: Path, clock: Clock, probe: RecordingTokenProbe) -> FileTokenProvider:
    return FileTokenProvider(
        "digimon", token_file, check_interval_seconds=5, clock=clock, probe=probe
    )


@pytest.fixture
def upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(
    provider: FileTokenProvider, probe: RecordingTokenProbe
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BASE_URL, auth=BearerAuth(provider, probe=probe)) as http:
        yield http


def bearer(request: httpx.Request) -> str:
    return request.headers["Authorization"]


# FileTokenProvider


async def test_reads_the_token_and_strips_the_trailing_newline(provider, probe):
    assert (await provider.token()).get_secret_value() == SECRET
    assert probe.events == ["loaded digimon"]


async def test_does_not_touch_the_file_again_within_the_check_interval(provider, token_file, clock):
    await provider.token()
    rotate(token_file, ROTATED)
    clock.now = 4.9

    assert (await provider.token()).get_secret_value() == SECRET


async def test_picks_up_a_rotated_secret_after_the_check_interval(
    provider, token_file, clock, probe
):
    await provider.token()
    rotate(token_file, ROTATED)
    clock.now = 5

    assert (await provider.token()).get_secret_value() == ROTATED
    assert probe.events == ["loaded digimon", "loaded digimon"]


async def test_an_unchanged_file_is_not_reported_as_a_new_token(provider, clock, probe):
    await provider.token()
    clock.now = 60
    await provider.token()

    assert probe.events == ["loaded digimon"]


async def test_invalidate_checks_the_file_again_immediately(provider, token_file):
    await provider.token()
    rotate(token_file, ROTATED)

    await provider.invalidate()

    assert (await provider.token()).get_secret_value() == ROTATED


async def test_concurrent_callers_share_one_read(provider, probe):
    tokens = await asyncio.gather(*(provider.token() for _ in range(20)))

    assert {t.get_secret_value() for t in tokens} == {SECRET}
    assert probe.events == ["loaded digimon"]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda path: path.unlink(), "cannot read the token file (FileNotFoundError)"),
        (lambda path: path.write_text("  \n", encoding="utf-8"), "the token file is empty"),
    ],
)
async def test_a_missing_or_empty_file_means_no_token(provider, token_file, probe, setup, reason):
    setup(token_file)

    with pytest.raises(TokenUnavailable, match="no access token for digimon") as excinfo:
        await provider.token()

    assert excinfo.value.reason == reason
    assert probe.events == [f"unavailable digimon: {reason}"]


def test_availability_is_whether_the_file_exists(provider, token_file):
    assert provider.available() is True
    token_file.unlink()
    assert provider.available() is False


async def test_static_provider_returns_its_token_and_rejects_an_empty_one():
    assert (
        await StaticTokenProvider("digimon", SecretStr(SECRET)).token()
    ).get_secret_value() == SECRET
    empty = StaticTokenProvider("digimon", SecretStr(" "))
    assert empty.available() is False
    with pytest.raises(TokenUnavailable, match="empty"):
        await empty.token()


# BearerAuth


async def test_sends_the_token_as_a_bearer_header(client, upstream):
    route = upstream.get("/digimon/agumon").respond(200, json={})

    await client.get("/digimon/agumon")

    assert bearer(route.calls.last.request) == f"Bearer {SECRET}"


async def test_on_401_retries_once_with_a_rotated_token(
    client, provider, upstream, token_file, probe
):
    route = upstream.get("/digimon/agumon")
    route.side_effect = [httpx.Response(401), httpx.Response(200, json={})]
    await provider.token()  # the first token is loaded before it rotates
    rotate(token_file, ROTATED)

    response = await client.get("/digimon/agumon")

    assert response.status_code == 200
    assert [bearer(call.request) for call in route.calls] == [
        f"Bearer {SECRET}",
        f"Bearer {ROTATED}",
    ]
    assert "rejected" not in " ".join(probe.events)


async def test_on_401_without_a_newer_token_does_not_retry(client, upstream, probe):
    route = upstream.get("/digimon/agumon").respond(401)

    response = await client.get("/digimon/agumon")

    assert response.status_code == 401
    assert route.call_count == 1
    assert probe.events[-1] == "rejected digimon retried=False"


async def test_a_rotated_token_that_is_also_refused_is_reported(
    client, provider, upstream, token_file, probe
):
    route = upstream.get("/digimon/agumon").respond(401)
    await provider.token()
    rotate(token_file, ROTATED)

    response = await client.get("/digimon/agumon")

    assert response.status_code == 401
    assert route.call_count == 2
    assert probe.events[-1] == "rejected digimon retried=True"


def test_sync_clients_are_refused(provider):
    with (
        httpx.Client(auth=BearerAuth(provider)) as http,
        pytest.raises(RuntimeError, match="AsyncClient"),
    ):
        http.get("https://digimon.test/")


# The secret never leaks


async def test_the_token_is_not_in_spans_or_logs(provider, upstream, caplog):
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    caplog.set_level(logging.DEBUG)
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    upstream.get("/digimon/agumon").respond(401)
    async with httpx.AsyncClient(
        base_url=BASE_URL, auth=BearerAuth(provider, probe=LoggingTokenProbe())
    ) as http:
        HTTPXClientInstrumentor.instrument_client(http, tracer_provider=tracer_provider)
        await http.get("/digimon/agumon")

    recorded = [
        str(value)
        for span in spans.get_finished_spans()
        for value in [
            *(span.attributes or {}).values(),
            *(v for event in span.events for v in (event.attributes or {}).values()),
        ]
    ]
    assert spans.get_finished_spans()
    assert "rejected the access token" in caplog.text
    assert all(SECRET not in value for value in recorded)
    assert SECRET not in caplog.text
    assert SECRET not in repr(await provider.token())
