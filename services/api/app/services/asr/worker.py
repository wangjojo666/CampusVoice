from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from typing import Any, Literal, cast

from app.services.asr.adapters import (
    AsrProviderError,
    AsrSessionConfig,
    FunAsrAdapter,
    TranscriptResult,
    WhisperAdapter,
)


class ProviderLifecycle(StrEnum):
    CREATED = "created"
    OPEN = "open"
    FINALIZING = "finalizing"
    FINISHED = "finished"
    ABORTED = "aborted"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    provider: Literal["funasr", "whisper"]
    model_name: str
    device: str
    vad_model: str | None = None
    punc_model: str | None = None


WorkerTarget = Callable[[Connection, ProviderSpec], None]


@dataclass(slots=True)
class _IdleWorker:
    spec: ProviderSpec
    target: WorkerTarget
    connection: Connection
    process: BaseProcess


def _close_connection_quietly(connection: Any) -> None:
    with contextlib.suppress(AttributeError, OSError):
        connection.close()


def _terminate_worker(
    process: BaseProcess,
    connection: Connection,
    *,
    terminate_timeout_seconds: float,
    kill_timeout_seconds: float,
) -> None:
    if process.pid is not None:
        process.join(timeout=0)
        if process.is_alive():
            process.terminate()
            process.join(timeout=terminate_timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(timeout=kill_timeout_seconds)
        if process.is_alive():
            raise RuntimeError("ASR worker survived terminate and kill")
    process.close()
    _close_connection_quietly(connection)


def _build_worker_adapter(spec: ProviderSpec) -> FunAsrAdapter | WhisperAdapter:
    if spec.provider == "funasr":
        return FunAsrAdapter(
            model_name=spec.model_name,
            vad_model=spec.vad_model,
            punc_model=spec.punc_model,
            device=spec.device,
        )
    return WhisperAdapter(model_name=spec.model_name, device=spec.device)


def provider_worker_main(connection: Connection, spec: ProviderSpec) -> None:
    """Own one complete provider session inside a spawn-created process."""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    adapter = _build_worker_adapter(spec)
    try:
        while True:
            try:
                operation, payload = connection.recv()
            except EOFError:
                return
            operation_succeeded = False
            try:
                if operation == "start":
                    result: object = loop.run_until_complete(
                        adapter.start(AsrSessionConfig(**payload))
                    )
                elif operation == "feed":
                    result = loop.run_until_complete(adapter.feed(payload))
                elif operation == "flush":
                    result = loop.run_until_complete(adapter.flush())
                elif operation == "finish":
                    result = loop.run_until_complete(adapter.finish())
                elif operation == "observe_vad":
                    observe_vad = getattr(adapter, "observe_vad", None)
                    result = (
                        loop.run_until_complete(observe_vad(payload))
                        if callable(observe_vad)
                        else None
                    )
                elif operation == "reset_vad":
                    reset_vad = getattr(adapter, "reset_vad", None)
                    result = loop.run_until_complete(reset_vad()) if callable(reset_vad) else None
                elif operation == "reset_utterance":
                    reset_utterance = getattr(adapter, "reset_utterance", None)
                    result = (
                        loop.run_until_complete(reset_utterance())
                        if callable(reset_utterance)
                        else None
                    )
                elif operation == "close":
                    result = loop.run_until_complete(adapter.close())
                else:
                    raise RuntimeError(f"unknown provider worker operation: {operation}")
            except AsrProviderError as exc:
                connection.send(
                    {
                        "ok": False,
                        "code": exc.code,
                        "message": exc.message,
                        "recoverable": exc.recoverable,
                    }
                )
            except BaseException:
                connection.send(
                    {
                        "ok": False,
                        "code": "provider_worker_failed",
                        "message": "语音识别工作进程失败，请重试。",
                        "recoverable": False,
                    }
                )
            else:
                operation_succeeded = True
                if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
                    serialized: object = [
                        asdict(item) if isinstance(item, TranscriptResult) else item
                        for item in result
                    ]
                else:
                    serialized = result
                connection.send({"ok": True, "result": serialized})
            if operation == "close":
                if not operation_succeeded:
                    return
                # Recreate session-owned state while retaining this process's model cache.
                adapter = _build_worker_adapter(spec)
    finally:
        connection.close()
        loop.close()


class AsrProcessSupervisor:
    def __init__(
        self,
        *,
        max_workers: int,
        max_waiters: int,
        admission_timeout_seconds: float,
        operation_timeout_seconds: float,
        finish_timeout_seconds: float,
        terminate_timeout_seconds: float,
        kill_timeout_seconds: float,
        context: SpawnContext | None = None,
    ) -> None:
        self._capacity = asyncio.Semaphore(max_workers)
        self._max_waiters = max_waiters
        self._waiters = 0
        self._admission_timeout_seconds = admission_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self.finish_timeout_seconds = finish_timeout_seconds
        self.terminate_timeout_seconds = terminate_timeout_seconds
        self.kill_timeout_seconds = kill_timeout_seconds
        self.context = context or multiprocessing.get_context("spawn")
        self._active: set[ProcessAsrAdapter] = set()
        self._idle: list[_IdleWorker] = []
        self._closing = False

    @property
    def active_workers(self) -> int:
        return len(self._active)

    @property
    def idle_workers(self) -> int:
        return len(self._idle)

    @property
    def waiting_sessions(self) -> int:
        return self._waiters

    async def acquire(self) -> None:
        if self._closing:
            raise AsrProviderError(
                "provider_shutting_down",
                "语音识别服务正在关闭。",
            )
        if self._capacity.locked():
            if self._waiters >= self._max_waiters:
                raise AsrProviderError(
                    "provider_queue_full",
                    "语音识别服务繁忙，请稍后重试。",
                    recoverable=True,
                )
            self._waiters += 1
            try:
                await asyncio.wait_for(
                    self._capacity.acquire(),
                    timeout=self._admission_timeout_seconds,
                )
            except TimeoutError as exc:
                raise AsrProviderError(
                    "provider_admission_timeout",
                    "等待语音识别资源超时，请稍后重试。",
                    recoverable=True,
                ) from exc
            finally:
                self._waiters -= 1
        else:
            await self._capacity.acquire()
        if self._closing:
            self._capacity.release()
            raise AsrProviderError(
                "provider_shutting_down",
                "语音识别服务正在关闭。",
            )

    def claim(self, spec: ProviderSpec, target: WorkerTarget) -> _IdleWorker | None:
        for index in range(len(self._idle) - 1, -1, -1):
            worker = self._idle[index]
            if worker.spec == spec and worker.target is target:
                self._idle.pop(index)
                if worker.process.is_alive():
                    return worker
                _terminate_worker(
                    worker.process,
                    worker.connection,
                    terminate_timeout_seconds=self.terminate_timeout_seconds,
                    kill_timeout_seconds=self.kill_timeout_seconds,
                )
        if self._idle:
            # Capacity is available for a new session, so evict one incompatible
            # idle process before spawning to preserve the global process bound.
            worker = self._idle.pop()
            _terminate_worker(
                worker.process,
                worker.connection,
                terminate_timeout_seconds=self.terminate_timeout_seconds,
                kill_timeout_seconds=self.kill_timeout_seconds,
            )
        return None

    def recycle(self, adapter: ProcessAsrAdapter) -> None:
        worker = adapter._detach_for_reuse()
        self._active.discard(adapter)
        self._idle.append(worker)
        self._capacity.release()

    def register(self, adapter: ProcessAsrAdapter) -> None:
        if self._closing:
            raise AsrProviderError(
                "provider_shutting_down",
                "语音识别服务正在关闭。",
            )
        self._active.add(adapter)

    def release(self, adapter: ProcessAsrAdapter) -> None:
        self._active.discard(adapter)
        self._capacity.release()

    async def close(self) -> None:
        self._closing = True
        failures: list[BaseException] = []
        for adapter in tuple(self._active):
            try:
                adapter.abort()
            except BaseException as exc:
                failures.append(exc)
        while self._idle:
            worker = self._idle.pop()
            try:
                _terminate_worker(
                    worker.process,
                    worker.connection,
                    terminate_timeout_seconds=self.terminate_timeout_seconds,
                    kill_timeout_seconds=self.kill_timeout_seconds,
                )
            except BaseException as exc:
                failures.append(exc)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("ASR worker shutdown failed", failures)


class ProcessAsrAdapter:
    """Proxy whose model, audio, cache, VAD, and locks all live in a child process."""

    hard_cancel_safe = True

    def __init__(
        self,
        supervisor: AsrProcessSupervisor,
        spec: ProviderSpec,
        *,
        worker_target: WorkerTarget = provider_worker_main,
        reuse_worker: bool | None = None,
    ) -> None:
        self.provider_name: str = spec.provider
        self._supervisor = supervisor
        self._spec = spec
        self._worker_target = worker_target
        self._reuse_worker = (
            worker_target is provider_worker_main if reuse_worker is None else reuse_worker
        )
        self._connection: Connection | None = None
        self._process: BaseProcess | None = None
        self._command_lock = asyncio.Lock()
        self._slot_acquired = False
        self._close_started = False
        self.lifecycle = ProviderLifecycle.CREATED

    @property
    def worker_pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def worker_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def resources_reclaimed(self) -> bool:
        return not self.worker_alive and not self._slot_acquired

    async def start(self, config: AsrSessionConfig) -> None:
        if self.lifecycle is not ProviderLifecycle.CREATED:
            raise AsrProviderError(
                "session_already_started", "语音识别会话已经开始。", recoverable=True
            )
        if self._slot_acquired and self.worker_alive:
            try:
                await self._request("start", asdict(config))
            except AsrProviderError as exc:
                if not exc.recoverable:
                    self.abort()
                raise
            self.lifecycle = ProviderLifecycle.OPEN
            return
        await self._supervisor.acquire()
        self._slot_acquired = True
        child: Any | None = None
        try:
            if self._reuse_worker:
                idle = self._supervisor.claim(self._spec, self._worker_target)
                if idle is not None:
                    self._connection = idle.connection
                    self._process = idle.process
            if self._process is None:
                parent, child = self._supervisor.context.Pipe(duplex=True)
                process = self._supervisor.context.Process(
                    target=self._worker_target,
                    args=(child, self._spec),
                    daemon=True,
                    name=f"campusvoice-asr-{self.provider_name}",
                )
                self._connection = cast(Connection, parent)
                self._process = process
                process.start()
                _close_connection_quietly(child)
            self._supervisor.register(self)
            await self._request("start", asdict(config))
        except AsrProviderError as exc:
            _close_connection_quietly(child)
            if exc.recoverable and self.worker_alive:
                raise
            self.abort()
            raise
        except BaseException:
            _close_connection_quietly(child)
            self.abort()
            raise
        self.lifecycle = ProviderLifecycle.OPEN

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        return self._deserialize_results(await self._request("feed", pcm_s16le))

    async def flush(self) -> Sequence[TranscriptResult]:
        return self._deserialize_results(await self._request("flush", None))

    async def observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        result = await self._request("observe_vad", pcm_s16le)
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            return None
        return bool(result[0]), bool(result[1])

    async def reset_vad(self) -> None:
        if not self.worker_alive:
            return
        await self._request("reset_vad", None)

    async def reset_utterance(self) -> None:
        if not self.worker_alive:
            return
        await self._request("reset_utterance", None)

    async def finish(self) -> Sequence[TranscriptResult]:
        if self.lifecycle is not ProviderLifecycle.OPEN:
            return ()
        self.lifecycle = ProviderLifecycle.FINALIZING
        try:
            result = await self._request(
                "finish",
                None,
                deadline_seconds=self._supervisor.finish_timeout_seconds,
                timeout_code="provider_finish_timeout",
                timeout_message="语音识别收尾超时。",
            )
        except BaseException:
            self.lifecycle = ProviderLifecycle.ABORTED
            raise
        self.lifecycle = ProviderLifecycle.FINISHED
        return self._deserialize_results(result)

    async def close(self) -> None:
        if self._close_started:
            return
        self._close_started = True
        failure: BaseException | None = None
        clean_close = False
        if self.worker_alive:
            try:
                await self._request(
                    "close",
                    None,
                    deadline_seconds=self._supervisor.terminate_timeout_seconds,
                    timeout_code="provider_close_timeout",
                    timeout_message="语音识别工作进程关闭超时。",
                )
                clean_close = True
            except BaseException as exc:
                if not self.resources_reclaimed:
                    failure = exc
        if clean_close and self._reuse_worker and failure is None and self.worker_alive:
            self._supervisor.recycle(self)
            self.lifecycle = ProviderLifecycle.CLOSED
            return

        try:
            self._reap_or_terminate()
        except BaseException as exc:
            if failure is None:
                failure = exc
            else:
                failure = BaseExceptionGroup("ASR provider close failed", [failure, exc])
        if not self.worker_alive:
            self.lifecycle = ProviderLifecycle.CLOSED
        if failure is not None:
            raise failure

    def _detach_for_reuse(self) -> _IdleWorker:
        process = self._process
        connection = self._connection
        if (
            process is None
            or connection is None
            or not process.is_alive()
            or not self._slot_acquired
        ):
            raise RuntimeError("ASR worker is not reusable")
        worker = _IdleWorker(self._spec, self._worker_target, connection, process)
        self._process = None
        self._connection = None
        self._slot_acquired = False
        return worker

    def abort(self) -> None:
        self.lifecycle = ProviderLifecycle.ABORTED
        self._reap_or_terminate()

    async def _request(
        self,
        operation: str,
        payload: object,
        *,
        deadline_seconds: float | None = None,
        timeout_code: str = "provider_operation_timeout",
        timeout_message: str = "语音识别操作超时。",
    ) -> object:
        async with self._command_lock:
            connection = self._connection
            process = self._process
            if connection is None or process is None or not process.is_alive():
                self.abort()
                raise AsrProviderError(
                    "provider_worker_exited",
                    "语音识别工作进程已退出，请重试。",
                )
            deadline = asyncio.get_running_loop().time() + (
                deadline_seconds or self._supervisor.operation_timeout_seconds
            )
            try:
                connection.send((operation, payload))
                while not connection.poll():
                    if not process.is_alive():
                        self.abort()
                        raise AsrProviderError(
                            "provider_worker_exited",
                            "语音识别工作进程已退出，请重试。",
                        )
                    if asyncio.get_running_loop().time() >= deadline:
                        self.abort()
                        raise AsrProviderError(timeout_code, timeout_message)
                    await asyncio.sleep(0.01)
                response = connection.recv()
            except asyncio.CancelledError:
                self.abort()
                raise
            except (EOFError, OSError) as exc:
                self.abort()
                raise AsrProviderError(
                    "provider_worker_exited",
                    "语音识别工作进程已退出，请重试。",
                ) from exc
            if not response.get("ok"):
                raise AsrProviderError(
                    str(response.get("code", "provider_worker_failed")),
                    str(response.get("message", "语音识别工作进程失败，请重试。")),
                    recoverable=bool(response.get("recoverable", False)),
                )
            return response.get("result")

    @staticmethod
    def _deserialize_results(raw: object) -> tuple[TranscriptResult, ...]:
        if not isinstance(raw, list):
            return ()
        return tuple(TranscriptResult(**item) for item in raw)

    def _reap_or_terminate(self) -> None:
        process = self._process
        connection = self._connection
        if process is not None:
            if process.pid is not None:
                process.join(timeout=0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=self._supervisor.terminate_timeout_seconds)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=self._supervisor.kill_timeout_seconds)
                if process.is_alive():
                    raise RuntimeError("ASR worker survived terminate and kill")
            process.close()
            self._process = None
        if connection is not None:
            connection.close()
            self._connection = None
        if self._slot_acquired:
            self._slot_acquired = False
            self._supervisor.release(self)
