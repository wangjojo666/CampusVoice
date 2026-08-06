import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from time import monotonic
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.observability import configure_sensitive_transport_logging
from app.security.wechat import WeChatClient, WeChatError, WeChatIdentity

_APP_ID = "wx3648488d39d15ff4"
_APP_SECRET = "b" * 32


def settings() -> Settings:
    return Settings(
        env="test",
        auth_mode="wechat",
        wechat_app_id=_APP_ID,
        wechat_app_secret=SecretStr(_APP_SECRET),
        confirmation_secret=SecretStr("test-confirmation-secret-with-32-characters"),
    )


@pytest.mark.asyncio
async def test_code_exchange_is_official_https_and_discards_session_key() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "openid": "private-openid",
                "unionid": "private-unionid",
                "session_key": "must-not-escape-client",
            },
        )

    identity = await WeChatClient(
        settings(),
        transport=httpx.MockTransport(handler),
    ).exchange_code("single-use-code")

    assert identity.openid == "private-openid"
    assert identity.unionid == "private-unionid"
    assert not hasattr(identity, "session_key")
    assert len(requests) == 1
    request = requests[0]
    parts = urlsplit(str(request.url))
    query: Mapping[str, list[str]] = parse_qs(parts.query)
    assert (parts.scheme, parts.hostname, parts.path) == (
        "https",
        "api.weixin.qq.com",
        "/sns/jscode2session",
    )
    assert query == {
        "appid": [_APP_ID],
        "secret": [_APP_SECRET],
        "js_code": ["single-use-code"],
        "grant_type": ["authorization_code"],
    }
    assert request.headers["accept"] == "application/json"


@pytest.mark.asyncio
async def test_code_exchange_credentials_are_redacted_from_httpx_info_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    login_code = "one-time-login-code-that-must-not-be-logged"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"openid": "openid-that-must-not-be-logged"})

    configure_sensitive_transport_logging()
    with caplog.at_level(logging.INFO, logger="httpx"):
        identity = await WeChatClient(
            settings(),
            transport=httpx.MockTransport(handler),
        ).exchange_code(login_code)

    assert identity.openid == "openid-that-must-not-be-logged"
    serialized = "\n".join(record.getMessage() for record in caplog.records)
    assert "jscode2session" in serialized
    assert _APP_SECRET not in serialized
    assert login_code not in serialized
    assert identity.openid not in serialized
    assert "secret=<redacted>" in serialized
    assert "js_code=<redacted>" in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, content=b"{}"), "wechat_identity_invalid"),
        (httpx.Response(200, content=b"not-json"), "wechat_response_invalid"),
        (
            httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"}),
            "wechat_code_rejected",
        ),
        (
            httpx.Response(302, headers={"Location": "https://attacker.example/final"}),
            "wechat_transport_rejected",
        ),
        (httpx.Response(200, content=b"x" * 65_537), "wechat_response_too_large"),
        (httpx.Response(503, content=b"unavailable"), "wechat_service_unavailable"),
    ],
    ids=[
        "missing-identity",
        "invalid-json",
        "rejected-code",
        "redirect-rejected",
        "oversized-response",
        "http-error",
    ],
)
async def test_code_exchange_failures_are_bounded(
    response: httpx.Response,
    expected_code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response

    with pytest.raises(WeChatError) as raised:
        await WeChatClient(
            settings(),
            transport=httpx.MockTransport(handler),
        ).exchange_code("single-use-code")

    assert raised.value.code == expected_code
    assert str(raised.value) == expected_code


@pytest.mark.asyncio
async def test_code_exchange_capacity_is_fail_fast_and_recovers() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json={"openid": "capacity-openid"})

    limited_settings = settings().model_copy(update={"wechat_code_exchange_max_concurrency": 1})
    client = WeChatClient(limited_settings, transport=httpx.MockTransport(handler))

    first = asyncio.create_task(client.exchange_code("first-code"))
    await started.wait()
    try:
        with pytest.raises(WeChatError) as raised:
            await client.exchange_code("second-code")
        assert raised.value.code == "wechat_exchange_capacity_exceeded"
        assert raised.value.retry_after_seconds == 1
    finally:
        release.set()

    assert await first == WeChatIdentity(openid="capacity-openid")
    assert await client.exchange_code("third-code") == WeChatIdentity(openid="capacity-openid")


@pytest.mark.asyncio
async def test_code_exchange_token_bucket_limits_sustained_requests_and_refills() -> None:
    now = [100.0]
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"openid": f"openid-{calls}"})

    limited_settings = settings().model_copy(
        update={
            "wechat_code_exchange_rate_per_second": 0.5,
            "wechat_code_exchange_burst": 2,
        }
    )
    client = WeChatClient(
        limited_settings,
        transport=httpx.MockTransport(handler),
        clock=lambda: now[0],
    )

    await client.exchange_code("first-code")
    await client.exchange_code("second-code")
    with pytest.raises(WeChatError) as raised:
        await client.exchange_code("third-code")

    assert raised.value.code == "wechat_exchange_rate_exceeded"
    assert raised.value.retry_after_seconds == 2
    assert calls == 2

    now[0] += 2.0
    assert await client.exchange_code("fourth-code") == WeChatIdentity(openid="openid-3")


class _SlowBody(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"{"
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_code_exchange_total_deadline_cancels_slow_drip_body() -> None:
    body = _SlowBody()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    deadline_settings = settings().model_copy(update={"wechat_http_timeout_seconds": 0.02})
    client = WeChatClient(deadline_settings, transport=httpx.MockTransport(handler))
    started_at = monotonic()

    with pytest.raises(WeChatError) as raised:
        await client.exchange_code("slow-code")

    assert raised.value.code == "wechat_service_unavailable"
    assert monotonic() - started_at < 0.5
    assert body.started.is_set()
    assert body.closed is True


@pytest.mark.asyncio
async def test_code_exchange_cancellation_closes_body_and_releases_capacity() -> None:
    slow_body = _SlowBody()
    slow_request = True

    async def handler(_request: httpx.Request) -> httpx.Response:
        if slow_request:
            return httpx.Response(200, stream=slow_body)
        return httpx.Response(200, json={"openid": "recovered-openid"})

    limited_settings = settings().model_copy(update={"wechat_code_exchange_max_concurrency": 1})
    client = WeChatClient(limited_settings, transport=httpx.MockTransport(handler))
    task = asyncio.create_task(client.exchange_code("cancelled-code"))
    await slow_body.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slow_body.closed is True

    slow_request = False
    assert await client.exchange_code("next-code") == WeChatIdentity(openid="recovered-openid")


@pytest.mark.asyncio
async def test_code_exchange_rejects_invalid_json_without_leaking_secret() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([_APP_SECRET]).encode())

    with pytest.raises(WeChatError) as raised:
        await WeChatClient(
            settings(),
            transport=httpx.MockTransport(handler),
        ).exchange_code("single-use-code")

    assert raised.value.code == "wechat_response_invalid"
    assert _APP_SECRET not in str(raised.value)
