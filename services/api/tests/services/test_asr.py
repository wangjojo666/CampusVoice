import asyncio
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.asr import AsrSessionConfig, TranscriptResult
from app.services.asr import adapters as asr_adapters
from app.services.asr.adapters import FunAsrAdapter, _FunAsrModelHandle
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
async def test_websocket_protocol_emits_interim_final_and_timing() -> None:
    adapter = StubAsrAdapter()
    socket = StubSocket(
        [
            {"type": "websocket.receive", "text": '{"type":"start","hotwords":["机器学习"]}'},
            {"type": "websocket.receive", "bytes": b"\xff\x7f" * 160},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ]
    )

    await handle_asr_websocket(socket, lambda: adapter)  # type: ignore[arg-type]

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

    async def close_persistence(_session_id: str) -> None:
        nonlocal persistence_close_calls
        persistence_close_calls += 1
        raise RuntimeError("persistence close failed")

    socket = StubSocket([{"type": "websocket.disconnect"}])

    with pytest.raises(RuntimeError, match="adapter close failed"):
        await handle_asr_websocket(  # type: ignore[arg-type]
            socket,
            lambda: adapter,
            close_hook=close_persistence,
        )

    assert adapter.close_calls == 1
    assert persistence_close_calls == 1
    assert socket.sent[-1]["code"] == "session_cleanup_failed"
    assert socket.close_code == 1011


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
