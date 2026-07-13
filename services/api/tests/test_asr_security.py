from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.routes import asr as asr_route
from app.core.config import Settings
from app.core.metrics import InMemoryMetrics
from app.services.asr import AsrSessionConfig, TranscriptResult
from app.services.asr.connections import AsrConnectionRegistry

ORIGIN = "http://localhost:3000"


class NoopAsrAdapter:
    provider_name = "test-security"

    async def start(self, config: AsrSessionConfig) -> None:
        del config

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        del pcm_s16le
        return ()

    async def flush(self) -> Sequence[TranscriptResult]:
        return ()

    async def finish(self) -> Sequence[TranscriptResult]:
        return ()

    async def close(self) -> None:
        return None


def _issue_ticket(client: TestClient) -> str:
    response = client.post("/api/auth/ws-ticket", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return str(response.json()["ticket"])


def _subprotocols(ticket: str) -> list[str]:
    return ["campusvoice", f"campusvoice.ticket.{ticket}"]


def _assert_handshake_rejected(
    client: TestClient,
    *,
    headers: dict[str, str],
    subprotocols: list[str],
    reason: str,
) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as raised,
        client.websocket_connect(
            "/ws/asr",
            headers=headers,
            subprotocols=subprotocols,
        ) as websocket,
    ):
        websocket.receive_json()
    assert raised.value.code == 1008
    assert raised.value.reason == reason


def test_control_message_limit_has_bounded_configuration() -> None:
    assert Settings(env="test").asr_max_control_message_bytes == 32_768
    with pytest.raises(ValidationError):
        Settings(env="test", asr_max_control_message_bytes=255)


def test_websocket_rejects_unconfigured_origin_before_authentication(client: TestClient) -> None:
    _assert_handshake_rejected(
        client,
        headers={"Origin": "https://evil.example"},
        subprotocols=["campusvoice", "campusvoice.ticket.not-a-real-ticket"],
        reason="origin_not_allowed",
    )


def test_websocket_requires_one_time_ticket(client: TestClient) -> None:
    _assert_handshake_rejected(
        client,
        headers={"Origin": ORIGIN},
        subprotocols=["campusvoice"],
        reason="authentication_required",
    )


def test_websocket_ticket_cannot_be_replayed(client: TestClient) -> None:
    client.app.state.asr_adapter_factory = NoopAsrAdapter
    ticket = _issue_ticket(client)

    with client.websocket_connect(
        "/ws/asr",
        headers={"Origin": ORIGIN},
        subprotocols=_subprotocols(ticket),
    ) as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1000

    _assert_handshake_rejected(
        client,
        headers={"Origin": ORIGIN},
        subprotocols=_subprotocols(ticket),
        reason="invalid_or_replayed_ticket",
    )


def test_connection_limit_rejects_second_session_and_releases_after_close(
    client: TestClient,
) -> None:
    client.app.state.asr_adapter_factory = NoopAsrAdapter
    first_ticket = _issue_ticket(client)
    second_ticket = _issue_ticket(client)

    with client.websocket_connect(
        "/ws/asr",
        headers={"Origin": ORIGIN},
        subprotocols=_subprotocols(first_ticket),
    ) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect(
            "/ws/asr",
            headers={"Origin": ORIGIN},
            subprotocols=_subprotocols(second_ticket),
        ) as second:
            rejected = second.receive_json()
            assert rejected["type"] == "error"
            assert rejected["code"] == "connection_limit_reached"
            with pytest.raises(WebSocketDisconnect) as closed:
                second.receive_json()
            assert closed.value.code == 1008
            assert closed.value.reason == "connection_limit_reached"
        first.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            first.receive_json()

    third_ticket = _issue_ticket(client)
    with client.websocket_connect(
        "/ws/asr",
        headers={"Origin": ORIGIN},
        subprotocols=_subprotocols(third_ticket),
    ) as third:
        assert third.receive_json()["type"] == "ready"
        third.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect):
            third.receive_json()

    component_metrics = client.get("/api/metrics").json()["components"]
    asr_ok = next(
        item for item in component_metrics if item["component"] == "asr" and item["outcome"] == "ok"
    )
    assert asr_ok["count"] >= 2


@pytest.mark.asyncio
async def test_route_releases_connection_quota_when_session_cleanup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseAdapter(NoopAsrAdapter):
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("adapter close failed")

    class FailingPersistence:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.close_calls = 0

        async def record_event(self, event: object) -> None:
            del event

        async def close(self, session_id: str) -> None:
            del session_id
            self.close_calls += 1
            raise RuntimeError("persistence close failed")

    class DummySession:
        async def __aenter__(self) -> "DummySession":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def get(self, model: object, key: str) -> None:
            del model, key
            return None

    class DummySessionFactory:
        def __call__(self) -> DummySession:
            return DummySession()

    class DummySocket:
        def __init__(self) -> None:
            self.adapter = FailingCloseAdapter()
            settings = Settings(env="test", database_auto_create=True)
            state = SimpleNamespace(
                settings=settings,
                session_factory=DummySessionFactory(),
                asr_connections=AsrConnectionRegistry(),
                asr_adapter_factory=lambda: self.adapter,
                metrics=InMemoryMetrics(),
            )
            self.app = SimpleNamespace(state=state)
            self.headers = {
                "origin": ORIGIN,
                "sec-websocket-protocol": "campusvoice, campusvoice.ticket.cleanup-test",
            }
            self.sent: list[dict[str, Any]] = []
            self.close_code: int | None = None

        async def receive(self) -> dict[str, str]:
            return {"type": "websocket.disconnect"}

        async def close(self, *, code: int, reason: str = "") -> None:
            del reason
            self.close_code = code

        async def accept(self, *, subprotocol: str | None = None) -> None:
            del subprotocol

        async def send_json(self, payload: dict[str, Any]) -> None:
            self.sent.append(payload)

    async def consume_ticket(*args: object, **kwargs: object) -> str:
        del args, kwargs
        return "user-cleanup"

    persistence = FailingPersistence()
    monkeypatch.setattr(asr_route, "consume_websocket_ticket", consume_ticket)
    monkeypatch.setattr(
        asr_route,
        "SqlAlchemyAsrPersistence",
        lambda *args, **kwargs: persistence,
    )
    socket = DummySocket()

    with pytest.raises(RuntimeError, match="adapter close failed"):
        await asr_route.asr_websocket(socket)  # type: ignore[arg-type]

    registry = socket.app.state.asr_connections
    assert await registry.count("user-cleanup") == 0
    assert socket.adapter.close_calls == 1
    assert persistence.close_calls == 1
    assert socket.sent[-1]["code"] == "session_cleanup_failed"
    assert socket.close_code == 1011
    errors = [
        item
        for item in socket.app.state.metrics.snapshot()["components"]
        if item["component"] == "asr" and item["outcome"] == "error"
    ]
    assert errors[0]["count"] == 1
