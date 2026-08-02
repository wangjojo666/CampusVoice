from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Sequence
from functools import partial
from multiprocessing.connection import Connection
from typing import Any

import pytest

from app.schemas.asr import AsrServerEvent
from app.services.asr.adapters import AsrProviderError
from app.services.asr.session import handle_asr_websocket
from app.services.asr.worker import (
    AsrProcessSupervisor,
    ProcessAsrAdapter,
    ProviderSpec,
)


def _finish_blocking_worker(
    events: Any,
    connection: Connection,
    _spec: ProviderSpec,
) -> None:
    while True:
        operation, _payload = connection.recv()
        events.put((operation, os.getpid()))
        if operation == "finish":
            threading.Event().wait()
        connection.send({"ok": True, "result": [] if operation == "finish" else None})
        if operation == "close":
            return


class _Socket:
    def __init__(self, messages: Sequence[dict[str, Any]]) -> None:
        self.messages = list(messages)
        self.sent: list[dict[str, Any]] = []
        self.close_code: int | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        del subprotocol

    async def receive(self) -> dict[str, Any]:
        return self.messages.pop(0)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


def _supervisor() -> AsrProcessSupervisor:
    return AsrProcessSupervisor(
        max_workers=1,
        max_waiters=1,
        admission_timeout_seconds=0.1,
        operation_timeout_seconds=2,
        finish_timeout_seconds=0.05,
        terminate_timeout_seconds=0.2,
        kill_timeout_seconds=0.2,
    )


def _factory(supervisor: AsrProcessSupervisor, events: Any) -> ProcessAsrAdapter:
    return ProcessAsrAdapter(
        supervisor,
        ProviderSpec(provider="whisper", model_name="test", device="cpu"),
        worker_target=partial(_finish_blocking_worker, events),
    )


@pytest.mark.asyncio
async def test_normal_stop_finish_timeout_reports_1011_and_incomplete() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _factory(supervisor, events)
    socket = _Socket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completed: list[bool] = []

    async def close_persistence(_session_id: str, value: bool) -> None:
        completed.append(value)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        close_hook=close_persistence,
    )

    assert socket.sent[-1]["code"] == "provider_finish_timeout"
    assert socket.sent[-1]["recoverable"] is False
    assert socket.close_code == 1011
    assert completed == [False]
    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0


@pytest.mark.asyncio
async def test_disconnect_finish_timeout_persists_error_without_socket_write() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _factory(supervisor, events)
    socket = _Socket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.disconnect"},
        ]
    )
    persisted: list[AsrServerEvent] = []
    completed: list[bool] = []

    async def persist(event: AsrServerEvent) -> None:
        persisted.append(event)

    async def close_persistence(_session_id: str, value: bool) -> None:
        completed.append(value)

    with pytest.raises(AsrProviderError) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            event_hook=persist,
            close_hook=close_persistence,
        )

    assert caught.value.code == "provider_finish_timeout"
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None
    assert persisted[-1].code == "provider_finish_timeout"
    assert completed == [False]
    assert adapter.resources_reclaimed


@pytest.mark.asyncio
async def test_disconnect_timeout_keeps_primary_error_with_persistence_and_close_failures() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _factory(supervisor, events)
    socket = _Socket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.disconnect"},
        ]
    )
    persistence_failure = RuntimeError("terminal persistence failed")
    close_failure = RuntimeError("persistence close failed")

    async def persist(event: AsrServerEvent) -> None:
        if event.type == "error":
            raise persistence_failure

    async def close_persistence(_session_id: str, _completed: bool) -> None:
        raise close_failure

    with pytest.raises(BaseExceptionGroup) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            event_hook=persist,
            close_hook=close_persistence,
        )

    assert isinstance(caught.value.exceptions[0], AsrProviderError)
    assert caught.value.exceptions[0].code == "provider_finish_timeout"  # type: ignore[union-attr]
    assert caught.value.exceptions[1:] == (persistence_failure, close_failure)
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None
    assert adapter.resources_reclaimed


@pytest.mark.asyncio
async def test_handler_cancellation_reaps_worker_and_reraises_original() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _factory(supervisor, events)
    socket = _Socket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completed: list[bool] = []

    async def close_persistence(_session_id: str, value: bool) -> None:
        completed.append(value)

    task = asyncio.create_task(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
        )
    )
    start_event = await asyncio.to_thread(events.get, True, 3)
    finish_event = await asyncio.to_thread(events.get, True, 3)
    assert start_event[0] == "start"
    assert finish_event[0] == "finish"

    task.cancel("server shutdown")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("server shutdown",)
    assert completed == [False]
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None
    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0
