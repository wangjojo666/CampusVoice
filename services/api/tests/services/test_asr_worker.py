from __future__ import annotations

import asyncio
import ctypes
import os
from functools import partial
from multiprocessing.connection import Connection
from queue import Empty
from typing import Any

import pytest

from app.services.asr.adapters import AsrProviderError, AsrSessionConfig
from app.services.asr.worker import (
    AsrProcessSupervisor,
    ProcessAsrAdapter,
    ProviderSpec,
)


def _scripted_worker(
    events: Any,
    blocking_operation: str | None,
    connection: Connection,
    _spec: ProviderSpec,
) -> None:
    while True:
        operation, _payload = connection.recv()
        events.put((operation, os.getpid()))
        if operation == blocking_operation:
            while True:
                threading_event = __import__("threading").Event()
                threading_event.wait()
        connection.send({"ok": True, "result": [] if operation == "finish" else None})
        if operation == "close":
            return


def _pid_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
            handle, ctypes.byref(exit_code)
        ):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


def _supervisor(
    *,
    max_workers: int = 1,
    max_waiters: int = 1,
    admission_timeout: float = 0.1,
    operation_timeout: float = 2.0,
    finish_timeout: float = 0.1,
) -> AsrProcessSupervisor:
    return AsrProcessSupervisor(
        max_workers=max_workers,
        max_waiters=max_waiters,
        admission_timeout_seconds=admission_timeout,
        operation_timeout_seconds=operation_timeout,
        finish_timeout_seconds=finish_timeout,
        terminate_timeout_seconds=0.2,
        kill_timeout_seconds=0.2,
    )


def _adapter(
    supervisor: AsrProcessSupervisor,
    events: Any,
    *,
    blocking_operation: str | None = None,
) -> ProcessAsrAdapter:
    return ProcessAsrAdapter(
        supervisor,
        ProviderSpec(provider="whisper", model_name="test", device="cpu"),
        worker_target=partial(_scripted_worker, events, blocking_operation),
    )


async def _next_event(events: Any) -> tuple[str, int]:
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        try:
            return events.get_nowait()
        except Empty:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("timed out waiting for worker event") from None
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_permanently_blocked_finish_is_killed_and_pid_disappears() -> None:
    supervisor = _supervisor(finish_timeout=0.05)
    events = supervisor.context.Queue()
    adapter = _adapter(supervisor, events, blocking_operation="finish")
    await adapter.start(AsrSessionConfig())
    pid = adapter.worker_pid
    assert pid is not None

    with pytest.raises(AsrProviderError) as caught:
        await adapter.finish()

    assert caught.value.code == "provider_finish_timeout"
    assert not _pid_is_running(pid)
    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0
    await adapter.close()


@pytest.mark.asyncio
async def test_capacity_is_released_only_after_worker_death() -> None:
    supervisor = _supervisor(finish_timeout=0.05)
    events = supervisor.context.Queue()
    adapter = _adapter(supervisor, events, blocking_operation="finish")
    release_observations: list[bool] = []
    original_release = supervisor.release

    def checked_release(target: ProcessAsrAdapter) -> None:
        release_observations.append(target.worker_alive)
        original_release(target)

    supervisor.release = checked_release  # type: ignore[method-assign]
    await adapter.start(AsrSessionConfig())

    with pytest.raises(AsrProviderError, match="收尾超时"):
        await adapter.finish()

    assert release_observations == [False]
    assert supervisor.active_workers == 0


@pytest.mark.asyncio
async def test_cancellation_reaps_worker_and_preserves_cancelled_error() -> None:
    supervisor = _supervisor(finish_timeout=2.0)
    events = supervisor.context.Queue()
    adapter = _adapter(supervisor, events, blocking_operation="finish")
    await adapter.start(AsrSessionConfig())
    pid = adapter.worker_pid
    assert pid is not None
    task = asyncio.create_task(adapter.finish())
    assert (await _next_event(events))[0] == "start"
    assert (await _next_event(events))[0] == "finish"

    task.cancel("original cancellation")
    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert caught.value.args == ("original cancellation",)
    assert not _pid_is_running(pid)
    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0


@pytest.mark.asyncio
async def test_close_is_exactly_once_and_after_finish() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _adapter(supervisor, events)
    await adapter.start(AsrSessionConfig())
    pid = adapter.worker_pid
    assert pid is not None
    await adapter.finish()
    await adapter.close()
    await adapter.close()

    observed = [await _next_event(events) for _ in range(3)]
    assert [operation for operation, _pid in observed] == ["start", "finish", "close"]
    assert not _pid_is_running(pid)
    assert supervisor.active_workers == 0


@pytest.mark.asyncio
async def test_worker_and_admission_queue_remain_bounded() -> None:
    supervisor = _supervisor(admission_timeout=0.05)
    events = supervisor.context.Queue()
    first = _adapter(supervisor, events)
    second = _adapter(supervisor, events)
    rejected = _adapter(supervisor, events)
    await first.start(AsrSessionConfig())
    waiting = asyncio.create_task(second.start(AsrSessionConfig()))
    await asyncio.sleep(0)

    with pytest.raises(AsrProviderError) as caught:
        await rejected.start(AsrSessionConfig())
    assert caught.value.code == "provider_queue_full"
    with pytest.raises(AsrProviderError) as waiting_error:
        await waiting
    assert waiting_error.value.code == "provider_admission_timeout"
    assert supervisor.active_workers == 1
    assert supervisor.waiting_sessions == 0
    await first.close()
    assert supervisor.active_workers == 0


@pytest.mark.asyncio
async def test_shutdown_forcibly_reaps_active_worker() -> None:
    supervisor = _supervisor()
    events = supervisor.context.Queue()
    adapter = _adapter(supervisor, events)
    await adapter.start(AsrSessionConfig())
    pid = adapter.worker_pid
    assert pid is not None

    await supervisor.close()

    assert not _pid_is_running(pid)
    assert adapter.resources_reclaimed
    assert supervisor.active_workers == 0
