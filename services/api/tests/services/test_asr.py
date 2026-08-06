import asyncio
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.asr import AsrProviderError, AsrSessionConfig, TranscriptResult
from app.services.asr import adapters as asr_adapters
from app.services.asr.adapters import FunAsrAdapter, _FunAsrModelHandle
from app.services.asr.audio import AudioDecodeError
from app.services.asr.session import PcmEnergyVad, handle_asr_websocket


class StubAsrAdapter:
    """Protocol stub; deliberately scoped to tests and never used as production output."""

    provider_name = "test-only"

    def __init__(self) -> None:
        self.started_with: AsrSessionConfig | None = None
        self.closed = False

    async def start(self, config: AsrSessionConfig) -> None:
        self.started_with = config

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        assert pcm_s16le == b"\xff\x7f" * 160
        return (TranscriptResult("机器", 0.72, 15.5, 10.0, False),)

    async def flush(self) -> Sequence[TranscriptResult]:
        return ()

    async def finish(self) -> Sequence[TranscriptResult]:
        return (TranscriptResult("机器学习", 0.91, 32.0, 10.0, True),)

    async def close(self) -> None:
        self.closed = True


class StubSocket:
    def __init__(self, incoming: list[dict[str, Any]]) -> None:
        self.incoming = iter(incoming)
        self.sent: list[dict[str, Any]] = []
        self.accepted = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        return next(self.incoming)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int) -> None:
        self.close_code = code


class BlockingSocket(StubSocket):
    async def receive(self) -> dict[str, Any]:
        try:
            return next(self.incoming)
        except StopIteration:
            await asyncio.Event().wait()
            raise AssertionError("unreachable") from None


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted_subprotocol", [None, "campusvoice"])
async def test_hanging_accept_returns_before_allocating_session_resources(
    accepted_subprotocol: str | None,
) -> None:
    class HangingAcceptSocket(StubSocket):
        def __init__(self) -> None:
            super().__init__([])
            self.accept_calls: list[str | None] = []

        async def accept(self, subprotocol: str | None = None) -> None:
            self.accept_calls.append(subprotocol)
            await asyncio.Event().wait()

    socket = HangingAcceptSocket()
    factory_calls = 0
    close_calls = 0

    def factory() -> StubAsrAdapter:
        nonlocal factory_calls
        factory_calls += 1
        return StubAsrAdapter()

    async def close_persistence(_session_id: str, _completed: bool) -> None:
        nonlocal close_calls
        close_calls += 1

    await asyncio.wait_for(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            factory,
            close_hook=close_persistence,
            accepted_subprotocol=accepted_subprotocol,
            max_session_seconds=0.05,
            cleanup_grace_seconds=0.01,
        ),
        timeout=0.2,
    )

    assert socket.accept_calls == [accepted_subprotocol]
    assert factory_calls == 0
    assert close_calls == 0
    assert socket.sent == []
    assert socket.close_code is None


@pytest.mark.asyncio
async def test_websocket_protocol_emits_interim_final_and_timing() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","hotwords":["机器学习"]}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    completion_states: list[bool] = []

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket, lambda: adapter, close_hook=close_persistence
    )

    assert socket.accepted
    assert socket.close_code == 1000
    assert adapter.closed
    assert adapter.started_with is not None
    assert adapter.started_with.hotwords == ("机器学习",)
    assert [item["type"] for item in socket.sent] == [
        "ready",
        "speech_start",
        "interim",
        "final",
        "speech_end",
    ]
    interim = socket.sent[2]
    final = socket.sent[3]
    assert interim["confidence"] == 0.72
    assert interim["latency_ms"] == 15.5
    assert final["text"] == "机器学习"
    assert final["audio_duration_ms"] == 10.0
    assert completion_states == [True]


@pytest.mark.asyncio
async def test_mp3_frames_are_bounded_and_decoded_only_after_stop() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"start","audio_format":"mp3"}',
            },
            {"type": "websocket.receive", "bytes": b"mp3-frame-a"},
            {"type": "websocket.receive", "bytes": b"mp3-frame-b"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    decode_calls: list[tuple[bytes, float]] = []

    async def decode_mp3(payload: bytes, max_seconds: float) -> bytes:
        decode_calls.append((payload, max_seconds))
        assert adapter.started_with is not None
        return b"\xff\x7f" * 160

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        mp3_decoder=decode_mp3,
    )

    assert decode_calls == [(b"mp3-frame-amp3-frame-b", 300.0)]
    assert socket.close_code == 1000
    assert adapter.closed is True
    assert [item["type"] for item in socket.sent] == [
        "ready",
        "finalizing",
        "speech_start",
        "interim",
        "final",
        "speech_end",
    ]


@pytest.mark.asyncio
async def test_mp3_finalization_heartbeats_are_ordered_and_not_persisted() -> None:
    class HeartbeatSocket(StubSocket):
        def __init__(self, incoming: list[dict[str, Any]]) -> None:
            super().__init__(incoming)
            self.two_heartbeats = asyncio.Event()

        async def send_json(self, payload: dict[str, Any]) -> None:
            await super().send_json(payload)
            if sum(item["type"] == "finalizing" for item in self.sent) >= 2:
                self.two_heartbeats.set()

    decoder_started = asyncio.Event()
    release_decoder = asyncio.Event()
    socket = HeartbeatSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    persisted: list[Any] = []

    async def decode(_payload: bytes, _max_seconds: float) -> bytes:
        decoder_started.set()
        await release_decoder.wait()
        return b"\xff\x7f" * 160

    async def persist(event: Any) -> None:
        persisted.append(event)

    task = asyncio.create_task(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            StubAsrAdapter,
            event_hook=persist,
            mp3_decoder=decode,
            finalizing_heartbeat_seconds=0.001,
        )
    )
    await decoder_started.wait()
    await asyncio.wait_for(socket.two_heartbeats.wait(), timeout=0.2)
    release_decoder.set()
    await task

    assert sum(item["type"] == "finalizing" for item in socket.sent) >= 2
    assert all(event.type != "finalizing" for event in persisted)
    assert [item["sequence"] for item in socket.sent] == list(range(len(socket.sent)))
    assert socket.close_code == 1000


@pytest.mark.asyncio
async def test_mp3_encoded_audio_limit_rejects_before_decode() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"start","audio_format":"mp3"}',
            },
            {"type": "websocket.receive", "bytes": b"12345"},
        ]
    )
    decode_called = False

    async def decode_mp3(_payload: bytes, _max_seconds: float) -> bytes:
        nonlocal decode_called
        decode_called = True
        return b"\x00\x00"

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        max_audio_seconds=0.0001,
        mp3_decoder=decode_mp3,
    )

    assert decode_called is False
    assert socket.sent[-1]["code"] == "encoded_audio_too_large"
    assert socket.sent[-1]["recoverable"] is False
    assert socket.close_code == 1008
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_mp3_decode_cannot_outlive_total_session_deadline() -> None:
    class CountingAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0
            self.finish_calls = 0

        async def finish(self) -> Sequence[TranscriptResult]:
            self.finish_calls += 1
            return await super().finish()

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = CountingAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completion_states: list[bool] = []

    async def blocked_decode(_payload: bytes, _max_seconds: float) -> bytes:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        close_hook=close_persistence,
        max_session_seconds=0.01,
        mp3_decoder=blocked_decode,
    )

    assert socket.sent[-1]["code"] == "session_duration_exceeded"
    assert socket.close_code == 1008
    assert adapter.finish_calls == 0
    assert adapter.close_calls == 1
    assert completion_states == [False]


@pytest.mark.asyncio
async def test_mp3_feed_cannot_accumulate_past_total_session_deadline() -> None:
    class SlowFeedAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
            del pcm_s16le
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = SlowFeedAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    async def decode(_payload: bytes, _max_seconds: float) -> bytes:
        return b"\xff\x7f" * 160

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        max_session_seconds=0.01,
        mp3_decoder=decode,
    )

    assert socket.sent[-1]["code"] == "session_duration_exceeded"
    assert socket.close_code == 1008
    assert adapter.close_calls == 1


@pytest.mark.asyncio
async def test_mp3_decode_failure_and_disconnect_finalize_resources_once() -> None:
    class CountingAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0
            self.finish_calls = 0

        async def finish(self) -> Sequence[TranscriptResult]:
            self.finish_calls += 1
            return await super().finish()

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    for terminal_message, expected_close in [
        ({"type": "websocket.receive", "text": '{"type":"stop"}'}, 1003),
        ({"type": "websocket.disconnect"}, None),
    ]:
        adapter = CountingAdapter()
        socket = StubSocket(
            [
                {
                    "type": "websocket.receive",
                    "text": '{"type":"start","audio_format":"mp3"}',
                },
                {"type": "websocket.receive", "bytes": b"mp3"},
                terminal_message,
            ]
        )
        decode_calls = 0

        async def reject_decode(_payload: bytes, _max_seconds: float) -> bytes:
            nonlocal decode_calls
            decode_calls += 1
            raise AudioDecodeError("invalid_encoded_audio", "invalid")

        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda adapter=adapter: adapter,
            mp3_decoder=reject_decode,
        )

        assert socket.close_code == expected_close
        assert adapter.close_calls == 1
        assert adapter.finish_calls == 0
        assert decode_calls == (1 if expected_close == 1003 else 0)
        if expected_close == 1003:
            assert socket.sent[-1]["code"] == "invalid_encoded_audio"


@pytest.mark.asyncio
async def test_mp3_flush_is_recoverable_and_explicit_pcm_remains_streaming() -> None:
    mp3_adapter = StubAsrAdapter()
    mp3_socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "text": '{"type":"flush"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    async def decode(_payload: bytes, _max_seconds: float) -> bytes:
        return b"\xff\x7f" * 160

    await handle_asr_websocket(  # type: ignore[arg-type]
        mp3_socket,
        lambda: mp3_adapter,
        mp3_decoder=decode,
    )
    assert mp3_socket.sent[1]["code"] == "flush_unsupported_for_mp3"
    assert mp3_socket.sent[1]["recoverable"] is True
    assert mp3_socket.close_code == 1000

    pcm_adapter = StubAsrAdapter()
    pcm_socket = StubSocket(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"start","audio_format":"pcm_s16le"}',
            },
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    await handle_asr_websocket(pcm_socket, lambda: pcm_adapter)  # type: ignore[arg-type]
    assert pcm_socket.close_code == 1000
    assert [event["type"] for event in pcm_socket.sent][:3] == [
        "ready",
        "speech_start",
        "interim",
    ]


@pytest.mark.asyncio
async def test_final_persistence_failure_does_not_mark_session_completed() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completion_states: list[bool] = []

    async def persist_event(event: Any) -> None:
        if event.type == "final":
            raise RuntimeError("final persistence failed")

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        event_hook=persist_event,
        close_hook=close_persistence,
    )

    assert completion_states == [False]
    assert socket.close_code == 1011
    assert [item["code"] for item in socket.sent if item["type"] == "error"] == [
        "transcription_persistence_failed"
    ]
    assert socket.sent[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_stop_before_start_is_persisted_as_completed() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
    completion_states: list[bool] = []

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        close_hook=close_persistence,
    )

    assert completion_states == [True]
    assert socket.close_code == 1000
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_finish_failure_does_not_mark_session_completed() -> None:
    failure = AsrProviderError(
        "finish_failed",
        "provider failed while finishing",
        recoverable=True,
    )

    class FinishFailureAdapter(StubAsrAdapter):
        async def finish(self) -> Sequence[TranscriptResult]:
            raise failure

    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completion_states: list[bool] = []
    persisted: list[Any] = []

    async def record_event(event: Any) -> None:
        persisted.append(event)

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        FinishFailureAdapter,
        event_hook=record_event,
        close_hook=close_persistence,
    )

    assert completion_states == [False]
    assert socket.close_code == 1011
    assert socket.sent[-1]["code"] == "finish_failed"
    assert persisted[-1].code == "finish_failed"
    assert persisted[-1].recoverable is False


@pytest.mark.asyncio
async def test_audio_before_start_is_recoverable_protocol_error() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "bytes": b"\x00\x00"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

    assert socket.sent[1]["type"] == "error"
    assert socket.sent[1]["code"] == "session_not_started"
    assert socket.sent[1]["recoverable"] is True


@pytest.mark.asyncio
async def test_odd_pcm_frame_is_rejected_before_vad_without_disconnect() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

    assert socket.sent[1]["type"] == "error"
    assert socket.sent[1]["code"] == "invalid_audio_frame"
    assert socket.sent[1]["recoverable"] is True
    assert socket.close_code == 1000


@pytest.mark.asyncio
async def test_oversized_audio_frame_is_rejected_even_before_start() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket([{"type": "websocket.receive", "bytes": b"123456"}])

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        max_frame_bytes=4,
    )

    assert socket.sent[-1]["code"] == "audio_frame_too_large"
    assert socket.sent[-1]["recoverable"] is False
    assert socket.close_code == 1009
    assert adapter.closed
    assert adapter.started_with is None


@pytest.mark.asyncio
async def test_cumulative_audio_duration_limit_rejects_frame_before_feed() -> None:
    adapter = StubAsrAdapter()
    pcm = b"\xff\x7f" * 160
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": pcm},
            {"type": "websocket.receive", "bytes": pcm},
        ]
    )

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        max_audio_seconds=0.015,
    )

    assert [item["code"] for item in socket.sent if item["type"] == "error"] == [
        "audio_duration_exceeded"
    ]
    assert socket.close_code == 1008
    assert adapter.closed


@pytest.mark.asyncio
async def test_oversized_control_message_has_distinct_fatal_error() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"ping","padding":"too-large"}',
            }
        ]
    )

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        max_control_message_bytes=16,
    )

    assert socket.sent[-1]["code"] == "control_message_too_large"
    assert socket.sent[-1]["recoverable"] is False
    assert socket.close_code == 1009
    assert adapter.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("idle_timeout", "session_timeout", "expected_code"),
    [
        (0.01, 1.0, "session_idle_timeout"),
        (1.0, 0.01, "session_duration_exceeded"),
    ],
)
async def test_session_time_limits_emit_stable_error_codes(
    idle_timeout: float,
    session_timeout: float,
    expected_code: str,
) -> None:
    adapter = StubAsrAdapter()
    socket = BlockingSocket([])

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        idle_timeout_seconds=idle_timeout,
        max_session_seconds=session_timeout,
    )

    assert socket.sent[-1]["code"] == expected_code
    assert socket.sent[-1]["recoverable"] is False
    assert socket.close_code == 1008
    assert adapter.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incoming", "idle_timeout", "expected_code"),
    [
        ([], 0.01, "session_idle_timeout"),
        ([{"type": "websocket.receive"}], 1.0, "invalid_frame"),
    ],
)
async def test_backpressured_terminal_send_cannot_block_cleanup(
    incoming: list[dict[str, Any]],
    idle_timeout: float,
    expected_code: str,
) -> None:
    class HangingTerminalSocket(BlockingSocket):
        def __init__(self, messages: list[dict[str, Any]]) -> None:
            super().__init__(messages)
            self.close_calls = 0

        async def send_json(self, payload: dict[str, Any]) -> None:
            await super().send_json(payload)
            if payload.get("code") == expected_code:
                await asyncio.Event().wait()

        async def close(self, code: int) -> None:
            self.close_calls += 1
            self.close_code = code
            await asyncio.Event().wait()

    class CountingAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = CountingAdapter()
    socket = HangingTerminalSocket(incoming)
    completion_states: list[bool] = []

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await asyncio.wait_for(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
            idle_timeout_seconds=idle_timeout,
            max_session_seconds=1.0,
            cleanup_grace_seconds=0.01,
        ),
        timeout=0.2,
    )

    assert any(item.get("code") == expected_code for item in socket.sent)
    assert socket.close_calls == 1
    assert adapter.close_calls == 1
    assert completion_states == [False]


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_stalled_mp3_finalization() -> None:
    class HangingHeartbeatSocket(StubSocket):
        def __init__(self, incoming: list[dict[str, Any]]) -> None:
            super().__init__(incoming)
            self.close_calls = 0

        async def send_json(self, payload: dict[str, Any]) -> None:
            await super().send_json(payload)
            if sum(item["type"] == "finalizing" for item in self.sent) >= 2:
                await asyncio.Event().wait()

        async def close(self, code: int) -> None:
            self.close_calls += 1
            await super().close(code)

    class CountingAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = CountingAdapter()
    socket = HangingHeartbeatSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    completion_states: list[bool] = []

    async def decode(_payload: bytes, _max_seconds: float) -> bytes:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    started_at = time.monotonic()
    await asyncio.wait_for(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
            max_session_seconds=1.0,
            finalizing_heartbeat_seconds=0.01,
            cleanup_grace_seconds=0.01,
            mp3_decoder=decode,
        ),
        timeout=0.2,
    )

    assert time.monotonic() - started_at < 0.2
    assert sum(item["type"] == "finalizing" for item in socket.sent) == 2
    assert socket.close_calls == 1
    assert adapter.close_calls == 1
    assert completion_states == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", ["flush", "finish"])
async def test_provider_error_reset_obeys_remaining_session_deadline(
    failed_stage: str,
) -> None:
    class HangingResetAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0
            self.reset_calls = 0

        async def flush(self) -> Sequence[TranscriptResult]:
            if failed_stage == "flush":
                raise AsrProviderError("flush_failed", "flush failed", recoverable=True)
            return await super().flush()

        async def finish(self) -> Sequence[TranscriptResult]:
            if failed_stage == "finish":
                raise AsrProviderError("finish_failed", "finish failed", recoverable=False)
            return await super().finish()

        async def reset_utterance(self) -> None:
            self.reset_calls += 1
            await asyncio.Event().wait()

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = HangingResetAdapter()
    incoming = (
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"flush"}'},
        ]
        if failed_stage == "flush"
        else [
            {"type": "websocket.receive", "text": '{"type":"start","audio_format":"mp3"}'},
            {"type": "websocket.receive", "bytes": b"mp3"},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    socket = StubSocket(incoming)
    completion_states: list[bool] = []

    async def decode(_payload: bytes, _max_seconds: float) -> bytes:
        return b"\xff\x7f" * 160

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await asyncio.wait_for(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
            max_session_seconds=0.05,
            cleanup_grace_seconds=0.01,
            mp3_decoder=decode,
        ),
        timeout=0.2,
    )

    assert adapter.reset_calls == 1
    assert adapter.close_calls == 1
    assert socket.close_code == 1008
    assert completion_states == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["finish", "persist"])
async def test_disconnect_salvage_obeys_remaining_session_deadline(
    blocked_stage: str,
) -> None:
    class DeadlineAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.finish_calls = 0
            self.close_calls = 0

        async def finish(self) -> Sequence[TranscriptResult]:
            self.finish_calls += 1
            if blocked_stage == "finish":
                await asyncio.Event().wait()
            return await super().finish()

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    adapter = DeadlineAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.disconnect"},
        ]
    )
    persisted_final_calls = 0
    completion_states: list[bool] = []

    async def persist(event: Any) -> None:
        nonlocal persisted_final_calls
        if event.type == "final":
            persisted_final_calls += 1
            if blocked_stage == "persist":
                await asyncio.Event().wait()

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    await asyncio.wait_for(
        handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            event_hook=persist,
            close_hook=close_persistence,
            max_session_seconds=0.01,
            cleanup_grace_seconds=0.01,
        ),
        timeout=0.2,
    )

    assert adapter.finish_calls == 1
    assert adapter.close_calls == 1
    assert persisted_final_calls == (1 if blocked_stage == "persist" else 0)
    assert completion_states == [False]


@pytest.mark.asyncio
async def test_cleanup_steps_have_independent_bounded_grace_and_run_once() -> None:
    class HangingCleanupReportSocket(StubSocket):
        async def send_json(self, payload: dict[str, Any]) -> None:
            await super().send_json(payload)
            if payload.get("code") == "session_cleanup_failed":
                await asyncio.Event().wait()

    class HangingCloseAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            await asyncio.Event().wait()

    adapter = HangingCloseAdapter()
    socket = HangingCleanupReportSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )
    persistence_close_calls = 0
    completion_states: list[bool] = []

    async def hanging_close_persistence(_session_id: str, completed: bool) -> None:
        nonlocal persistence_close_calls
        persistence_close_calls += 1
        completion_states.append(completed)
        await asyncio.Event().wait()

    with pytest.raises(BaseExceptionGroup, match="ASR session cleanup failed") as caught:
        await asyncio.wait_for(
            handle_asr_websocket(  # type: ignore[arg-type]
                socket,
                lambda: adapter,
                close_hook=hanging_close_persistence,
                cleanup_grace_seconds=0.01,
            ),
            timeout=0.2,
        )

    assert [type(error) for error in caught.value.exceptions] == [TimeoutError, TimeoutError]
    assert adapter.close_calls == 1
    assert persistence_close_calls == 1
    assert completion_states == [False]
    assert socket.sent[-1]["code"] == "session_cleanup_failed"
    assert socket.close_code == 1011


@pytest.mark.asyncio
async def test_adapter_and_persistence_cleanup_are_each_attempted_once_on_failure() -> None:
    class FailingCloseAdapter(StubAsrAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("adapter close failed")

    adapter = FailingCloseAdapter()
    persistence_close_calls = 0

    async def close_persistence(_session_id: str, _completed: bool) -> None:
        nonlocal persistence_close_calls
        persistence_close_calls += 1
        raise RuntimeError("persistence close failed")

    socket = StubSocket([{"type": "websocket.disconnect"}])

    with pytest.raises(ExceptionGroup, match="ASR session cleanup failed") as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
        )

    assert [type(error) for error in caught.value.exceptions] == [RuntimeError, RuntimeError]
    assert [str(error) for error in caught.value.exceptions] == [
        "adapter close failed",
        "persistence close failed",
    ]
    assert adapter.close_calls == 1
    assert persistence_close_calls == 1
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None


@pytest.mark.asyncio
async def test_single_cleanup_failure_preserves_original_exception() -> None:
    failure = RuntimeError("adapter close failed")

    class FailingCloseAdapter(StubAsrAdapter):
        async def close(self) -> None:
            raise failure

    socket = StubSocket([{"type": "websocket.disconnect"}])

    with pytest.raises(RuntimeError) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            FailingCloseAdapter,
        )

    assert caught.value is failure
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None


@pytest.mark.asyncio
async def test_primary_failure_is_preserved_when_cleanup_also_fails() -> None:
    finish_failure = RuntimeError("adapter finish failed")
    close_failure = RuntimeError("adapter close failed")

    class FinishAndCloseFailureAdapter(StubAsrAdapter):
        async def finish(self) -> Sequence[TranscriptResult]:
            raise finish_failure

        async def close(self) -> None:
            raise close_failure

    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    with pytest.raises(ExceptionGroup, match="ASR session and cleanup failed") as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            FinishAndCloseFailureAdapter,
        )

    assert caught.value.exceptions == (finish_failure, close_failure)
    assert socket.sent[-1]["code"] == "session_cleanup_failed"
    assert socket.close_code == 1011


@pytest.mark.asyncio
async def test_cleanup_report_cancellation_preserves_primary_and_close_failures() -> None:
    finish_failure = RuntimeError("adapter finish failed")
    close_failure = RuntimeError("adapter close failed")
    report_cancellation = asyncio.CancelledError("cleanup report cancelled")

    class FailingAdapter(StubAsrAdapter):
        async def finish(self) -> Sequence[TranscriptResult]:
            raise finish_failure

        async def close(self) -> None:
            raise close_failure

    class CancelledCleanupReportSocket(StubSocket):
        async def send_json(self, payload: dict[str, Any]) -> None:
            if payload.get("code") == "session_cleanup_failed":
                raise report_cancellation
            await super().send_json(payload)

    socket = CancelledCleanupReportSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    with pytest.raises(BaseExceptionGroup, match="ASR session and cleanup failed") as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            FailingAdapter,
        )

    assert caught.value.exceptions == (
        finish_failure,
        close_failure,
        report_cancellation,
    )
    assert socket.close_code == 1011


@pytest.mark.asyncio
async def test_cleanup_report_exception_group_preserves_existing_failures() -> None:
    finish_failure = RuntimeError("adapter finish failed")
    close_failure = RuntimeError("adapter close failed")
    report_cancellation = asyncio.CancelledError("cleanup report cancelled")
    report_failure = RuntimeError("cleanup report failed")
    report_group = BaseExceptionGroup(
        "cleanup report failures",
        [report_cancellation, report_failure],
    )

    class FailingAdapter(StubAsrAdapter):
        async def finish(self) -> Sequence[TranscriptResult]:
            raise finish_failure

        async def close(self) -> None:
            raise close_failure

    class GroupedCleanupReportSocket(StubSocket):
        async def send_json(self, payload: dict[str, Any]) -> None:
            if payload.get("code") == "session_cleanup_failed":
                raise report_group
            await super().send_json(payload)

    socket = GroupedCleanupReportSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    with pytest.raises(BaseExceptionGroup, match="ASR session and cleanup failed") as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            FailingAdapter,
        )

    assert caught.value.exceptions == (
        finish_failure,
        close_failure,
        report_group,
    )
    assert socket.close_code == 1011


@pytest.mark.asyncio
async def test_cancelled_adapter_cleanup_still_closes_persistence() -> None:
    cancellation = asyncio.CancelledError("adapter close cancelled")

    class CancelledCloseAdapter(StubAsrAdapter):
        async def close(self) -> None:
            raise cancellation

    socket = StubSocket([{"type": "websocket.disconnect"}])
    completion_states: list[bool] = []

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    with pytest.raises(asyncio.CancelledError) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            CancelledCloseAdapter,
            close_hook=close_persistence,
        )

    assert caught.value is cancellation
    assert completion_states == [False]
    assert [item["type"] for item in socket.sent] == ["ready"]
    assert socket.close_code is None


@pytest.mark.asyncio
async def test_abnormal_disconnect_drains_tail_without_marking_session_completed() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.disconnect"},
        ]
    )
    persisted: list[Any] = []
    close_calls: list[tuple[str, bool]] = []

    async def record_event(event: Any) -> None:
        persisted.append(event)

    async def close_persistence(session_id: str, completed: bool) -> None:
        close_calls.append((session_id, completed))

    await handle_asr_websocket(  # type: ignore[arg-type]
        socket,
        lambda: adapter,
        event_hook=record_event,
        close_hook=close_persistence,
    )

    assert adapter.closed is True
    assert [event.type for event in persisted] == ["ready", "speech_start", "interim", "final"]
    assert persisted[-1].text
    assert [item["type"] for item in socket.sent] == [
        "ready",
        "speech_start",
        "interim",
    ]
    assert len(close_calls) == 1
    assert close_calls[0][0] == persisted[0].session_id
    assert close_calls[0][1] is False


@pytest.mark.asyncio
async def test_abnormal_disconnect_persistence_error_is_not_reclassified() -> None:
    persistence_failure = AsrProviderError(
        "persistence_failed",
        "persistence hook used the provider error type",
        recoverable=False,
    )
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.disconnect"},
        ]
    )
    persisted: list[Any] = []

    async def record_event(event: Any) -> None:
        if event.type == "final":
            raise persistence_failure
        persisted.append(event)

    with pytest.raises(AsrProviderError) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            event_hook=record_event,
        )

    assert caught.value is persistence_failure
    assert all(event.type != "error" for event in persisted)
    assert [item["type"] for item in socket.sent] == ["ready", "speech_start", "interim"]
    assert socket.close_code is None


@pytest.mark.asyncio
async def test_abnormal_disconnect_finish_failure_is_reported_and_not_completed() -> None:
    failure = AsrProviderError(
        "finish_failed",
        "provider failed while draining the final transcript",
        recoverable=False,
    )

    class FinishFailureAdapter(StubAsrAdapter):
        async def finish(self) -> Sequence[TranscriptResult]:
            raise failure

    adapter = FinishFailureAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.disconnect"},
        ]
    )
    completion_states: list[bool] = []
    persisted: list[Any] = []

    async def record_event(event: Any) -> None:
        persisted.append(event)

    async def close_persistence(_session_id: str, completed: bool) -> None:
        completion_states.append(completed)

    with pytest.raises(AsrProviderError) as caught:
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            event_hook=record_event,
            close_hook=close_persistence,
        )

    assert caught.value is failure
    assert adapter.closed is True
    assert completion_states == [False]
    assert persisted[-1].code == "finish_failed"
    assert persisted[-1].recoverable is False
    assert [item["type"] for item in socket.sent] == ["ready", "speech_start", "interim"]
    assert socket.close_code is None


def test_energy_vad_reports_real_speech_boundaries() -> None:
    vad = PcmEnergyVad(sample_rate_hz=16_000, trailing_silence_ms=100)

    assert vad.observe(b"\xff\x7f" * 160) == (True, False)
    assert vad.observe(b"\x00\x00" * 800) == (False, False)
    assert vad.observe(b"\x00\x00" * 800) == (False, True)


def test_streaming_text_fragments_are_merged_without_duplication() -> None:
    assert FunAsrAdapter._merge_streaming_text("欢迎大家", "大家来体验") == "欢迎大家来体验"
    assert FunAsrAdapter._merge_streaming_text("机器学习", "机器学习考试") == "机器学习考试"
    assert FunAsrAdapter._merge_streaming_text("自然语言", "处理") == "自然语言处理"
    assert FunAsrAdapter._merge_streaming_text("学习", "习惯") == "学习习惯"
    assert FunAsrAdapter._merge_streaming_text("机器学习考试", "机器学习") == "机器学习考试"


def test_streaming_asr_vad_and_punctuation_models_are_loaded_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeAutoModel:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=FakeAutoModel))

    handle = _FunAsrModelHandle("paraformer-zh-streaming", "fsmn-vad", "ct-punc", "cpu")

    assert handle.model is not None
    assert [call["model"] for call in calls] == [
        "paraformer-zh-streaming",
        "fsmn-vad",
        "ct-punc",
    ]
    assert all("vad_model" not in call and "punc_model" not in call for call in calls)


def test_concurrent_first_connections_load_one_funasr_model_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asr_adapters._get_funasr_model_cached.cache_clear()
    constructions = 0
    barrier = threading.Barrier(4)

    class SlowHandle:
        def __init__(self, *args: object) -> None:
            nonlocal constructions
            del args
            constructions += 1
            time.sleep(0.05)

    monkeypatch.setattr(asr_adapters, "_FunAsrModelHandle", SlowHandle)

    def load() -> object:
        barrier.wait()
        return asr_adapters._get_funasr_model("concurrency-test", None, None, "cpu")

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            handles = list(executor.map(lambda _: load(), range(4)))
        assert constructions == 1
        assert all(handle is handles[0] for handle in handles)
    finally:
        asr_adapters._get_funasr_model_cached.cache_clear()


def test_model_vad_preserves_start_and_end_inside_one_large_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        is_speaking = False

        def __init__(self) -> None:
            self.calls = 0

        def feed(self, chunk: object) -> list[list[int]]:
            del chunk
            self.calls += 1
            if self.calls == 1:
                self.is_speaking = True
                return []
            self.is_speaking = False
            return [[0, 100]]

    # The core CI profile intentionally excludes the optional multi-gigabyte
    # Torch dependency.  This unit only verifies boundary aggregation; the
    # runtime stub ignores the tensor representation.
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(from_numpy=lambda value: value))
    handle = SimpleNamespace(vad_lock=threading.Lock())
    monkeypatch.setattr(asr_adapters, "_get_funasr_model", lambda *args: handle)
    adapter = FunAsrAdapter(
        model_name="asr",
        vad_model="vad",
        punc_model=None,
        device="cpu",
    )
    adapter._config = AsrSessionConfig()
    adapter._vad_runtime = Runtime()

    boundaries = adapter._observe_vad(b"\x00\x00" * (960 * 2))

    assert boundaries == (True, True)


@pytest.mark.asyncio
async def test_funasr_flush_is_idempotent_and_metrics_reset_per_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(
        [
            [{"text": "机器", "confidence": 0.7}],
            [{"text": "学习"}],
            [{"text": "考试", "confidence": 0.8}],
            [{"text": ""}],
        ]
    )

    class Model:
        calls = 0

        def generate(self, **kwargs: object) -> object:
            del kwargs
            self.calls += 1
            return next(outputs)

    model = Model()
    handle = SimpleNamespace(
        model=model,
        punc_model=None,
        lock=threading.Lock(),
        punc_lock=threading.Lock(),
    )
    monkeypatch.setattr(asr_adapters, "_get_funasr_model", lambda *args: handle)
    adapter = FunAsrAdapter(
        model_name="asr",
        vad_model=None,
        punc_model=None,
        device="cpu",
    )
    adapter._config = AsrSessionConfig()

    await adapter.feed(b"\x00\x00" * 1_600)  # 100 ms
    first_final = await adapter.flush()
    assert first_final[0].text == "机器学习"
    assert first_final[0].confidence == 0.7
    assert first_final[0].audio_duration_ms == 100.0
    assert await adapter.flush() == ()
    assert model.calls == 2

    await adapter.feed(b"\x00\x00" * 800)  # 50 ms
    second_final = await adapter.flush()
    assert second_final[0].text == "考试"
    assert second_final[0].confidence == 0.8
    assert second_final[0].audio_duration_ms == 50.0


class RecoverableVadFailureAdapter(StubAsrAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.vad_calls = 0

    async def observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        del pcm_s16le
        self.vad_calls += 1
        raise asr_adapters.AsrProviderError("vad_failed", "vad failed", recoverable=True)


@pytest.mark.asyncio
async def test_recoverable_model_vad_failure_falls_back_without_dropping_audio() -> None:
    adapter = RecoverableVadFailureAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

    assert adapter.vad_calls == 1
    assert [item["type"] for item in socket.sent] == [
        "ready",
        "error",
        "speech_start",
        "interim",
        "interim",
        "final",
        "speech_end",
    ]
    assert socket.close_code == 1000


class BoundaryFlushFailureAdapter(StubAsrAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.reset_calls = 0

    async def observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        del pcm_s16le
        return True, True

    async def flush(self) -> Sequence[TranscriptResult]:
        raise asr_adapters.AsrProviderError(
            "punctuation_failed",
            "punctuation failed",
            recoverable=True,
        )

    async def finish(self) -> Sequence[TranscriptResult]:
        return ()

    async def reset_utterance(self) -> None:
        self.reset_calls += 1


@pytest.mark.asyncio
async def test_boundary_flush_failure_reports_error_and_closes_speech_state() -> None:
    adapter = BoundaryFlushFailureAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

    assert [item["type"] for item in socket.sent] == [
        "ready",
        "speech_start",
        "interim",
        "error",
        "speech_end",
    ]
    assert adapter.reset_calls == 1
    assert socket.close_code == 1000


class MultiBoundaryAdapter(StubAsrAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.vad_events = iter([(True, False), (False, True), (True, False), (False, False)])
        self.feed_sizes: list[int] = []
        self.flush_calls = 0

    async def observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        assert len(pcm_s16le) == 1_920
        return next(self.vad_events)

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        self.feed_sizes.append(len(pcm_s16le))
        return ()

    async def flush(self) -> Sequence[TranscriptResult]:
        self.flush_calls += 1
        return (TranscriptResult("第一句", 0.8, 10.0, 120.0, True),)

    async def finish(self) -> Sequence[TranscriptResult]:
        return (TranscriptResult("第二句", 0.9, 12.0, 120.0, True),)


@pytest.mark.asyncio
async def test_large_websocket_frame_is_split_at_vad_granularity() -> None:
    adapter = MultiBoundaryAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start"}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * (960 * 4)},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

    assert adapter.feed_sizes == [1_920, 1_920, 1_920, 1_920]
    assert adapter.flush_calls == 1
    assert [item["type"] for item in socket.sent] == [
        "ready",
        "speech_start",
        "final",
        "speech_end",
        "speech_start",
        "final",
        "speech_end",
    ]
