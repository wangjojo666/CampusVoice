from __future__ import annotations

import asyncio
import ctypes
import os
from functools import partial
from multiprocessing.connection import Connection
from queue import Empty
from typing import Any

import pytest

from app.services.asr.adapters import AsrSessionConfig
from app.services.asr.worker import AsrProcessSupervisor, ProcessAsrAdapter, ProviderSpec


def _reusable_worker(events: Any, connection: Connection, _spec: ProviderSpec) -> None:
    session = 0
    while True:
        operation, _payload = connection.recv()
        if operation == "start":
            session += 1
        events.put((operation, os.getpid(), session))
        connection.send({"ok": True, "result": [] if operation == "finish" else None})


def _pid_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        0x1000,
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
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


async def _next_event(events: Any) -> tuple[str, int, int]:
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        try:
            return events.get_nowait()
        except Empty:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("timed out waiting for worker event") from None
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_clean_close_reuses_process_and_shutdown_reaps_idle_worker() -> None:
    supervisor = AsrProcessSupervisor(
        max_workers=1,
        max_waiters=1,
        admission_timeout_seconds=0.2,
        operation_timeout_seconds=2,
        finish_timeout_seconds=1,
        terminate_timeout_seconds=0.2,
        kill_timeout_seconds=0.2,
    )
    events = supervisor.context.Queue()
    spec = ProviderSpec(provider="whisper", model_name="test", device="cpu")
    worker_target = partial(_reusable_worker, events)

    first = ProcessAsrAdapter(supervisor, spec, worker_target=worker_target, reuse_worker=True)
    await first.start(AsrSessionConfig())
    _, first_pid, first_session = await _next_event(events)
    await first.finish()
    await first.close()
    assert (await _next_event(events))[0] == "finish"
    assert (await _next_event(events))[0] == "close"

    assert first.resources_reclaimed
    assert supervisor.active_workers == 0
    assert supervisor.idle_workers == 1

    second = ProcessAsrAdapter(supervisor, spec, worker_target=worker_target, reuse_worker=True)
    await second.start(AsrSessionConfig())
    _, second_pid, second_session = await _next_event(events)
