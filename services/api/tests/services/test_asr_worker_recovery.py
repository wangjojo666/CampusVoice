from __future__ import annotations

import asyncio
import multiprocessing
from functools import partial
from multiprocessing.connection import Connection
from typing import Any

import pytest

from app.services.asr.adapters import AsrProviderError, AsrSessionConfig
from app.services.asr.worker import (
    AsrProcessSupervisor,
    ProcessAsrAdapter,
    ProviderLifecycle,
    ProviderSpec,
)


def _healthy_worker(events: Any, connection: Connection, _spec: ProviderSpec) -> None:
    while True:
        operation, _payload = connection.recv()
        events.put(operation)
        connection.send({"ok": True, "result": [] if operation == "finish" else None})
        if operation == "close":
            return


def _retryable_start_worker(connection: Connection, _spec: ProviderSpec) -> None:
    starts = 0
    while True:
        operation, _payload = connection.recv()
        if operation == "start":
            starts += 1
            if starts == 1:
                connection.send(
                    {
                        "ok": False,
                        "code": "unsupported_audio_format",
                        "message": "bad format",
                        "recoverable": True,
                    }
                )
                continue
        connection.send({"ok": True, "result": [] if operation == "finish" else None})
        if operation == "close":
            return


class _StartFailingProcess:
    pid = None

    def start(self) -> None:
        raise RuntimeError("spawn failed before process start")

    def is_alive(self) -> bool:
        return False

    def close(self) -> None:
        return None


class _StartFailingContext:
    def __init__(self) -> None:
        self._delegate = multiprocessing.get_context("spawn")

    def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:  # noqa: N802
        return self._delegate.Pipe(duplex=duplex)

    def Process(self, **_kwargs: Any) -> _StartFailingProcess:  # noqa: N802
        return _StartFailingProcess()


def _supervisor(*, workers: int = 1) -> AsrProcessSupervisor:
    return AsrProcessSupervisor(
        max_workers=workers,
        max_waiters=1,
        admission_timeout_seconds=0.2,
        operation_timeout_seconds=2,
        finish_timeout_seconds=1,
        terminate_timeout_seconds=0.2,
        kill_timeout_seconds=0.2,
    )


def _adapter(
    supervisor: AsrProcessSupervisor,
    worker_target: Any,
) -> ProcessAsrAdapter:
    return ProcessAsrAdapter(
        supervisor,
        ProviderSpec(provider="whisper", model_name="test", device="cpu"),
        worker_target=worker_target,
    )


@pytest.mark.asyncio
async def test_free_worker_slots_do_not_consume_waiter_budget() -> None:
    supervisor = _supervisor(workers=2)
    events = supervisor.context.Queue()
    first = _adapter(supervisor, partial(_healthy_worker, events))
    second = _adapter(supervisor, partial(_healthy_worker, events))

    await asyncio.gather(
        first.start(AsrSessionConfig()),
        second.start(AsrSessionConfig()),
    )

    assert supervisor.active_workers == 2
    assert supervisor.waiting_sessions == 0
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_recoverable_start_failure_can_retry_in_same_worker() -> None:
    supervisor = _supervisor()
    adapter = _adapter(supervisor, _retryable_start_worker)

    with pytest.raises(AsrProviderError) as caught:
        await adapter.start(AsrSessionConfig(sample_rate_hz=8_000))
    assert caught.value.recoverable is True
    pid = adapter.worker_pid
    assert pid is not None

    await adapter.start(AsrSessionConfig())

    assert adapter.worker_pid == pid
    assert adapter.lifecycle is ProviderLifecycle.OPEN
    await adapter.close()
    assert adapter.resources_reclaimed


@pytest.mark.asyncio
async def test_spawn_failure_returns_capacity_without_joining_unstarted_process() -> None:
    supervisor = _supervisor()
    supervisor.context = _StartFailingContext()  # type: ignore[assignment]
    adapter = _adapter(supervisor, _retryable_start_worker)

    with pytest.raises(RuntimeError, match="spawn failed before process start"):
        await adapter.start(AsrSessionConfig())

    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0
