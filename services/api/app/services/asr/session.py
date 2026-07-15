import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from time import monotonic
from uuid import uuid4

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from app.schemas.asr import (
    AsrClientMessage,
    AsrEventType,
    AsrFlushMessage,
    AsrPingMessage,
    AsrServerEvent,
    AsrStartMessage,
    AsrStopMessage,
)
from app.services.asr.adapters import (
    AsrAdapter,
    AsrProviderError,
    AsrSessionConfig,
    TranscriptResult,
)

_CLIENT_MESSAGE_ADAPTER: TypeAdapter[AsrClientMessage] = TypeAdapter(AsrClientMessage)


class PcmEnergyVad:
    """Small deterministic VAD for protocol boundaries; ASR providers may also run model VAD."""

    def __init__(
        self,
        *,
        sample_rate_hz: int,
        energy_threshold: float = 0.015,
        trailing_silence_ms: int = 600,
    ) -> None:
        self._sample_rate_hz = sample_rate_hz
        self._energy_threshold = energy_threshold
        self._trailing_samples = int(sample_rate_hz * trailing_silence_ms / 1000)
        self._silent_samples = 0
        self.speaking = False

    def observe(self, pcm_s16le: bytes) -> tuple[bool, bool]:
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32)
        if samples.size == 0:
            return False, False
        normalized_rms = float(np.sqrt(np.mean(np.square(samples)))) / 32768.0
        voiced = normalized_rms >= self._energy_threshold
        started = False
        ended = False
        if voiced:
            self._silent_samples = 0
            if not self.speaking:
                self.speaking = True
                started = True
        elif self.speaking:
            self._silent_samples += int(samples.size)
            if self._silent_samples >= self._trailing_samples:
                self.speaking = False
                self._silent_samples = 0
                ended = True
        return started, ended

    def reset(self) -> bool:
        was_speaking = self.speaking
        self.speaking = False
        self._silent_samples = 0
        return was_speaking


class _EventSender:
    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        provider: str,
        event_hook: Callable[[AsrServerEvent], Awaitable[None]] | None = None,
    ) -> None:
        self.websocket = websocket
        self.session_id = session_id
        self.provider = provider
        self.sequence = 0
        self.event_hook = event_hook

    async def send(
        self,
        event_type: AsrEventType,
        *,
        text: str | None = None,
        confidence: float | None = None,
        latency_ms: float | None = None,
        audio_duration_ms: float | None = None,
        code: str | None = None,
        message: str | None = None,
        recoverable: bool | None = None,
    ) -> None:
        event = AsrServerEvent(
            type=event_type,
            session_id=self.session_id,
            sequence=self.sequence,
            provider=self.provider,
            text=text,
            confidence=confidence,
            latency_ms=latency_ms,
            audio_duration_ms=audio_duration_ms,
            code=code,
            message=message,
            recoverable=recoverable,
        )
        if self.event_hook is not None:
            try:
                await self.event_hook(event)
            except Exception:
                # Recognition may continue, but the client must know the audit
                # trail is unavailable. Disable the hook to avoid error floods.
                self.event_hook = None
                persistence_error = AsrServerEvent(
                    type="error",
                    session_id=self.session_id,
                    sequence=self.sequence,
                    provider=self.provider,
                    code="transcription_persistence_failed",
                    message="转写结果无法保存，当前会话不会用于数据写入。",
                    recoverable=False,
                )
                self.sequence += 1
                await self.websocket.send_json(persistence_error.model_dump(exclude_none=True))
                event.sequence = self.sequence
        self.sequence += 1
        await self.websocket.send_json(event.model_dump(exclude_none=True))

    async def transcripts(self, results: Sequence[TranscriptResult]) -> None:
        for result in results:
            await self.send(
                "final" if result.is_final else "interim",
                text=result.text,
                confidence=result.confidence,
                latency_ms=result.latency_ms,
                audio_duration_ms=result.audio_duration_ms,
            )


async def handle_asr_websocket(
    websocket: WebSocket,
    adapter_factory: Callable[[], AsrAdapter],
    *,
    event_hook: Callable[[AsrServerEvent], Awaitable[None]] | None = None,
    close_hook: Callable[[str], Awaitable[None]] | None = None,
    additional_hotwords: Sequence[str] = (),
    accepted_subprotocol: str | None = None,
    max_frame_bytes: int = 131_072,
    max_control_message_bytes: int = 32_768,
    idle_timeout_seconds: float = 30.0,
    max_session_seconds: float = 600.0,
    max_audio_seconds: float = 300.0,
) -> None:
    if accepted_subprotocol is None:
        await websocket.accept()
    else:
        await websocket.accept(subprotocol=accepted_subprotocol)
    session_id = str(uuid4())
    adapter: AsrAdapter | None = None
    sender: _EventSender | None = None
    started = False
    speaking = False
    vad: PcmEnergyVad | None = None
    provider_vad_enabled = True
    vad_frame_bytes: int | None = None
    adapter_closed = False
    persistence_closed = False
    opened_at = monotonic()
    audio_bytes_received = 0

    async def close_resources() -> list[Exception]:
        """Attempt every cleanup exactly once, even when an earlier close fails."""

        nonlocal adapter_closed, persistence_closed
        failures: list[Exception] = []
        if adapter is not None and not adapter_closed:
            adapter_closed = True
            try:
                await adapter.close()
            except Exception as exc:
                failures.append(exc)
        if close_hook is not None and not persistence_closed:
            persistence_closed = True
            try:
                await close_hook(session_id)
            except Exception as exc:
                failures.append(exc)
        return failures

    async def report_cleanup_failure() -> None:
        if sender is not None:
            with suppress(Exception):
                await sender.send(
                    "error",
                    code="session_cleanup_failed",
                    message="语音识别会话资源清理失败。",
                    recoverable=False,
                )
        with suppress(Exception):
            await websocket.close(code=1011)

    async def close_resources_or_raise() -> None:
        failures = await close_resources()
        if failures:
            await report_cleanup_failure()
            if len(failures) == 1:
                raise failures[0]
            raise ExceptionGroup("ASR session cleanup failed", failures)

    async def process_pcm_frame(pcm_frame: bytes) -> bool:
        """Process at most one 60 ms VAD frame; return True for a fatal provider error."""

        nonlocal provider_vad_enabled, speaking
        if adapter is None or sender is None:
            return True

        provider_events: tuple[bool, bool] | None = None
        provider_vad = getattr(adapter, "observe_vad", None)
        if provider_vad_enabled and callable(provider_vad):
            try:
                provider_events = await provider_vad(pcm_frame)
            except AsrProviderError as exc:
                await sender.send(
                    "error",
                    code=exc.code,
                    message=exc.message,
                    recoverable=exc.recoverable,
                )
                if not exc.recoverable:
                    return True
                # Continue recognition with deterministic energy VAD instead of
                # dropping the frame or repeating the same model error forever.
                provider_vad_enabled = False
        speech_started, speech_ended = (
            provider_events
            if provider_events is not None
            else (vad.observe(pcm_frame) if vad else (not speaking, False))
        )
        if speech_ended and not speaking:
            # A short frame may contain a complete model-VAD segment. Preserve
            # the protocol invariant that every speech_end has a speech_start.
            speech_started = True
        if speech_started and not speaking:
            speaking = True
            await sender.send("speech_start")

        feed_failed = False
        fatal_error = False
        try:
            await sender.transcripts(await adapter.feed(pcm_frame))
        except AsrProviderError as exc:
            feed_failed = True
            fatal_error = not exc.recoverable
            await sender.send(
                "error",
                code=exc.code,
                message=exc.message,
                recoverable=exc.recoverable,
            )
            reset_utterance = getattr(adapter, "reset_utterance", None)
            if callable(reset_utterance):
                await reset_utterance()
        if speech_ended:
            if not feed_failed:
                try:
                    await sender.transcripts(await adapter.flush())
                except AsrProviderError as exc:
                    fatal_error = not exc.recoverable
                    await sender.send(
                        "error",
                        code=exc.code,
                        message=exc.message,
                        recoverable=exc.recoverable,
                    )
                    reset_utterance = getattr(adapter, "reset_utterance", None)
                    if callable(reset_utterance):
                        await reset_utterance()
            if speaking:
                await sender.send("speech_end")
            speaking = False
        return fatal_error

    try:
        try:
            adapter = adapter_factory()
        except AsrProviderError as exc:
            sender = _EventSender(websocket, session_id, "unavailable", event_hook)
            await sender.send(
                "error", code=exc.code, message=exc.message, recoverable=exc.recoverable
            )
            await close_resources_or_raise()
            await websocket.close(code=1011)
            return
        sender = _EventSender(websocket, session_id, adapter.provider_name, event_hook)
        await sender.send("ready")

        while True:
            elapsed = monotonic() - opened_at
            remaining_session = max_session_seconds - elapsed
            if remaining_session <= 0:
                await sender.send(
                    "error",
                    code="session_duration_exceeded",
                    message="语音识别会话已达到最大时长。",
                    recoverable=False,
                )
                await websocket.close(code=1008)
                return
            try:
                session_deadline_is_limit = remaining_session <= idle_timeout_seconds
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=min(idle_timeout_seconds, remaining_session),
                )
            except TimeoutError:
                code = (
                    "session_duration_exceeded"
                    if session_deadline_is_limit
                    else "session_idle_timeout"
                )
                await sender.send(
                    "error",
                    code=code,
                    message=(
                        "语音识别会话已达到最大时长。"
                        if code == "session_duration_exceeded"
                        else "语音识别会话因长时间无数据而关闭。"
                    ),
                    recoverable=False,
                )
                await websocket.close(code=1008)
                return
            if message["type"] == "websocket.disconnect":
                break
            pcm = message.get("bytes")
            if pcm is not None:
                if len(pcm) > max_frame_bytes:
                    await sender.send(
                        "error",
                        code="audio_frame_too_large",
                        message="单个 PCM 音频帧超过服务端限制。",
                        recoverable=False,
                    )
                    await websocket.close(code=1009)
                    return
                if not started:
                    await sender.send(
                        "error",
                        code="session_not_started",
                        message="请先发送 start 控制消息。",
                        recoverable=True,
                    )
                    continue
                if not pcm:
                    continue
                if len(pcm) % 2:
                    await sender.send(
                        "error",
                        code="invalid_audio_frame",
                        message="PCM 音频帧长度必须是 2 字节的整数倍。",
                        recoverable=True,
                    )
                    continue
                bytes_per_second = 16_000 * 2
                projected_audio_bytes = audio_bytes_received + len(pcm)
                if projected_audio_bytes > int(max_audio_seconds * bytes_per_second):
                    await sender.send(
                        "error",
                        code="audio_duration_exceeded",
                        message="本次会话累计音频时长超过服务端限制。",
                        recoverable=False,
                    )
                    await websocket.close(code=1008)
                    return
                audio_bytes_received = projected_audio_bytes
                frame_size = vad_frame_bytes or len(pcm)
                for offset in range(0, len(pcm), frame_size):
                    if await process_pcm_frame(pcm[offset : offset + frame_size]):
                        await close_resources_or_raise()
                        await websocket.close(code=1011)
                        return
                continue

            raw_text = message.get("text")
            if raw_text is None:
                await sender.send(
                    "error",
                    code="invalid_frame",
                    message="仅支持 JSON 控制消息或二进制 PCM 音频帧。",
                    recoverable=True,
                )
                continue
            if len(raw_text.encode("utf-8", errors="replace")) > max_control_message_bytes:
                await sender.send(
                    "error",
                    code="control_message_too_large",
                    message="JSON 控制消息超过服务端限制。",
                    recoverable=False,
                )
                await websocket.close(code=1009)
                return
            try:
                parsed_json = json.loads(raw_text)
                control = _CLIENT_MESSAGE_ADAPTER.validate_python(parsed_json)
            except (json.JSONDecodeError, ValidationError):
                await sender.send(
                    "error",
                    code="invalid_control_message",
                    message="控制消息格式无效。",
                    recoverable=True,
                )
                continue

            if isinstance(control, AsrPingMessage):
                await sender.send("pong")
            elif isinstance(control, AsrStartMessage):
                if started:
                    await sender.send(
                        "error",
                        code="already_started",
                        message="语音会话已经开始。",
                        recoverable=True,
                    )
                    continue
                config = AsrSessionConfig(
                    sample_rate_hz=control.sample_rate_hz,
                    channels=control.channels,
                    sample_width_bytes=control.sample_width_bytes,
                    language=control.language,
                    hotwords=_merge_hotwords(additional_hotwords, control.hotwords),
                )
                try:
                    await adapter.start(config)
                except AsrProviderError as exc:
                    await sender.send(
                        "error",
                        code=exc.code,
                        message=exc.message,
                        recoverable=exc.recoverable,
                    )
                    if not exc.recoverable:
                        await close_resources_or_raise()
                        await websocket.close(code=1011)
                        return
                    continue
                started = True
                vad = PcmEnergyVad(sample_rate_hz=config.sample_rate_hz)
                vad_frame_bytes = int(config.sample_rate_hz * 0.06) * config.sample_width_bytes
            elif isinstance(control, AsrFlushMessage):
                if not started:
                    await sender.send(
                        "error",
                        code="session_not_started",
                        message="语音会话尚未开始。",
                        recoverable=True,
                    )
                    continue
                flush_fatal = False
                try:
                    await sender.transcripts(await adapter.flush())
                except AsrProviderError as exc:
                    flush_fatal = not exc.recoverable
                    await sender.send(
                        "error",
                        code=exc.code,
                        message=exc.message,
                        recoverable=exc.recoverable,
                    )
                    reset_utterance = getattr(adapter, "reset_utterance", None)
                    if callable(reset_utterance):
                        await reset_utterance()
                if speaking:
                    await sender.send("speech_end")
                    speaking = False
                if vad:
                    vad.reset()
                reset_provider_vad = getattr(adapter, "reset_vad", None)
                if callable(reset_provider_vad):
                    await reset_provider_vad()
                if flush_fatal:
                    await close_resources_or_raise()
                    await websocket.close(code=1011)
                    return
            elif isinstance(control, AsrStopMessage):
                close_code = 1000
                if started:
                    try:
                        await sender.transcripts(await adapter.finish())
                    except AsrProviderError as exc:
                        close_code = 1011
                        await sender.send(
                            "error",
                            code=exc.code,
                            message=exc.message,
                            recoverable=exc.recoverable,
                        )
                        reset_utterance = getattr(adapter, "reset_utterance", None)
                        if callable(reset_utterance):
                            await reset_utterance()
                    if speaking:
                        await sender.send("speech_end")
                    if vad:
                        vad.reset()
                    reset_provider_vad = getattr(adapter, "reset_vad", None)
                    if callable(reset_provider_vad):
                        await reset_provider_vad()
                await close_resources_or_raise()
                await websocket.close(code=close_code)
                return
    except WebSocketDisconnect:
        pass
    finally:
        await close_resources_or_raise()


def _merge_hotwords(*groups: Sequence[str], limit: int = 500) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            term = raw.strip()
            if not term or term in seen:
                continue
            seen.add(term)
            merged.append(term)
            if len(merged) >= limit:
                return tuple(merged)
    return tuple(merged)
