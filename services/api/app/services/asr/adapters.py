import asyncio
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from time import perf_counter
from typing import Any, Protocol

import numpy as np

from app.core.config import Settings


class AsrProviderError(RuntimeError):
    """A user-safe ASR provider failure."""

    def __init__(self, code: str, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recoverable = recoverable


@dataclass(frozen=True, slots=True)
class AsrSessionConfig:
    sample_rate_hz: int = 16_000
    channels: int = 1
    sample_width_bytes: int = 2
    language: str = "zh"
    hotwords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    text: str
    confidence: float
    latency_ms: float
    audio_duration_ms: float
    is_final: bool


class AsrAdapter(Protocol):
    provider_name: str

    async def start(self, config: AsrSessionConfig) -> None: ...

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]: ...

    async def flush(self) -> Sequence[TranscriptResult]: ...

    async def finish(self) -> Sequence[TranscriptResult]: ...

    async def close(self) -> None: ...


class DisabledAsrAdapter:
    provider_name = "disabled"

    async def start(self, config: AsrSessionConfig) -> None:
        del config
        raise AsrProviderError(
            "provider_disabled",
            "语音识别尚未启用，请在环境变量中配置 ASR 提供方。",
        )

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        del pcm_s16le
        return ()

    async def flush(self) -> Sequence[TranscriptResult]:
        return ()

    async def finish(self) -> Sequence[TranscriptResult]:
        return ()

    async def close(self) -> None:
        return None


class _FunAsrModelHandle:
    def __init__(
        self,
        model_name: str,
        vad_model: str | None,
        punc_model: str | None,
        device: str,
    ) -> None:
        try:
            from funasr import AutoModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AsrProviderError(
                "provider_dependency_missing",
                "FunASR 依赖未安装，无法启动语音识别。",
            ) from exc

        try:
            # FunASR's streaming Paraformer uses a three-part ASR chunk_size.  The
            # combined offline AutoModel pipeline forwards that value to FSMN-VAD,
            # whose streaming API expects a scalar millisecond chunk size.  Keep the
            # three production models independent and compose them at session level.
            quiet_options = {
                "device": device,
                "disable_update": True,
                "disable_pbar": True,
                "disable_log": True,
            }
            self.model = AutoModel(model=model_name, **quiet_options)
            self.vad_model = AutoModel(model=vad_model, **quiet_options) if vad_model else None
            self.punc_model = AutoModel(model=punc_model, **quiet_options) if punc_model else None
        except Exception as exc:
            raise AsrProviderError(
                "provider_initialization_failed",
                "FunASR 模型加载失败，请检查模型名称、设备和缓存。",
            ) from exc
        self.lock = threading.Lock()
        self.vad_lock = threading.Lock()
        self.punc_lock = threading.Lock()


_FUNASR_MODEL_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=4)
def _get_funasr_model_cached(
    model_name: str,
    vad_model: str | None,
    punc_model: str | None,
    device: str,
) -> _FunAsrModelHandle:
    return _FunAsrModelHandle(model_name, vad_model, punc_model, device)


def _get_funasr_model(
    model_name: str,
    vad_model: str | None,
    punc_model: str | None,
    device: str,
) -> _FunAsrModelHandle:
    """Load each model set once, including under concurrent first connections.

    ``functools.lru_cache`` keeps its mapping thread-safe but may execute the wrapped
    function more than once when concurrent misses race.  Model construction must be
    serialized because duplicate GPU loads can exhaust VRAM.
    """

    with _FUNASR_MODEL_LOAD_LOCK:
        return _get_funasr_model_cached(model_name, vad_model, punc_model, device)


class FunAsrAdapter:
    provider_name = "funasr"

    def __init__(
        self,
        *,
        model_name: str,
        vad_model: str | None,
        punc_model: str | None,
        device: str,
    ) -> None:
        self._model_name = model_name
        self._vad_model = vad_model
        self._punc_model = punc_model
        self._device = device
        self._config: AsrSessionConfig | None = None
        self._cache: dict[str, Any] = {}
        self._utterance_audio_bytes = 0
        self._utterance_confidence = 0.0
        self._closed = False
        self._vad_runtime: Any | None = None
        self._utterance_text = ""

    async def start(self, config: AsrSessionConfig) -> None:
        if config.sample_rate_hz != 16_000 or config.channels != 1:
            raise AsrProviderError(
                "unsupported_audio_format",
                "FunASR 当前仅接受 16 kHz 单声道 PCM 音频。",
                recoverable=True,
            )
        handle = await asyncio.to_thread(
            _get_funasr_model,
            self._model_name,
            self._vad_model,
            self._punc_model,
            self._device,
        )
        if handle.vad_model is not None:
            try:
                from funasr.models.fsmn_vad_streaming.dynamic_vad import (  # type: ignore[import-untyped]
                    DynamicStreamingVAD,
                )

                self._vad_runtime = DynamicStreamingVAD(
                    handle.vad_model,
                    chunk_size_ms=60,
                    sample_rate=config.sample_rate_hz,
                )
            except Exception as exc:
                raise AsrProviderError(
                    "vad_initialization_failed",
                    "FunASR VAD 初始化失败，请检查 VAD 模型配置。",
                ) from exc
        self._config = config

    def _observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        if self._vad_runtime is None or self._config is None:
            return None
        if len(pcm_s16le) % self._config.sample_width_bytes:
            raise AsrProviderError(
                "invalid_audio_frame",
                "PCM 音频帧长度必须是 2 字节的整数倍。",
                recoverable=True,
            )
        import torch

        handle = _get_funasr_model(
            self._model_name,
            self._vad_model,
            self._punc_model,
            self._device,
        )
        waveform = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        chunk_samples = int(self._config.sample_rate_hz * 0.06)
        started = False
        ended = False
        try:
            with handle.vad_lock:
                for offset in range(0, waveform.size, chunk_samples):
                    was_speaking = bool(self._vad_runtime.is_speaking)
                    chunk = torch.from_numpy(waveform[offset : offset + chunk_samples].copy())
                    segments = self._vad_runtime.feed(chunk)
                    is_speaking = bool(self._vad_runtime.is_speaking)
                    # A sufficiently large WebSocket frame can contain both boundaries.
                    # DynamicStreamingVAD only returns completed segments, so a segment
                    # produced while it began idle also proves a speech-start transition.
                    started = started or (not was_speaking and (is_speaking or bool(segments)))
                    ended = ended or ((was_speaking and not is_speaking) or bool(segments))
        except Exception as exc:
            raise AsrProviderError(
                "vad_failed",
                "语音活动检测失败，请重试。",
                recoverable=True,
            ) from exc
        return started, ended

    async def observe_vad(self, pcm_s16le: bytes) -> tuple[bool, bool] | None:
        """Return model VAD boundaries, or None when no VAD model is configured."""

        return await asyncio.to_thread(self._observe_vad, pcm_s16le)

    async def reset_vad(self) -> None:
        if self._vad_runtime is not None:
            self._vad_runtime.reset()

    @staticmethod
    def _result_text(raw: object) -> str:
        item = raw[0] if isinstance(raw, list) and raw else raw
        return str(item.get("text", "")) if isinstance(item, dict) else ""

    @staticmethod
    def _merge_streaming_text(existing: str, fragment: str) -> str:
        """Merge a streaming fragment without duplicating an overlapping suffix."""

        if not fragment:
            return existing
        if fragment.startswith(existing):
            return fragment
        if existing.startswith(fragment):
            return existing
        max_overlap = min(len(existing), len(fragment))
        # A one-character suffix/prefix match is commonly a legitimate repetition
        # (for example "学习" + "习惯"), not decoder overlap.
        for overlap in range(max_overlap, 1, -1):
            if existing.endswith(fragment[:overlap]):
                return existing + fragment[overlap:]
        return existing + fragment

    def _clear_utterance(self) -> None:
        self._cache = {}
        self._utterance_text = ""
        self._utterance_audio_bytes = 0
        self._utterance_confidence = 0.0

    async def reset_utterance(self) -> None:
        """Discard a failed utterance without affecting the shared production model."""

        self._clear_utterance()

    def _restore_punctuation(self, text: str) -> str:
        if not text:
            return text
        handle = _get_funasr_model(
            self._model_name,
            self._vad_model,
            self._punc_model,
            self._device,
        )
        if handle.punc_model is None:
            return text
        try:
            with handle.punc_lock:
                raw = handle.punc_model.generate(input=text)
        except Exception as exc:
            raise AsrProviderError(
                "punctuation_failed",
                "自动标点恢复失败，请重试。",
                recoverable=True,
            ) from exc
        restored = self._result_text(raw).strip()
        return restored or text

    def _generate(self, pcm_s16le: bytes, *, is_final: bool) -> TranscriptResult | None:
        if self._config is None:
            raise AsrProviderError("session_not_started", "语音会话尚未开始。", recoverable=True)
        handle = _get_funasr_model(
            self._model_name,
            self._vad_model,
            self._punc_model,
            self._device,
        )
        waveform = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        if waveform.size == 0 and not is_final:
            return None
        hotword = " ".join(self._config.hotwords)
        started = perf_counter()
        try:
            with handle.lock:
                raw = handle.model.generate(
                    input=waveform,
                    cache=self._cache,
                    is_final=is_final,
                    chunk_size=[0, 10, 5],
                    encoder_chunk_look_back=4,
                    decoder_chunk_look_back=1,
                    hotword=hotword or None,
                )
        except Exception as exc:
            raise AsrProviderError(
                "recognition_failed",
                "语音识别失败，请重试或检查音频输入。",
                recoverable=True,
            ) from exc
        latency_ms = (perf_counter() - started) * 1000
        item = raw[0] if isinstance(raw, list) and raw else raw
        fragment = self._result_text(raw).strip()
        self._utterance_text = self._merge_streaming_text(self._utterance_text, fragment)
        text = self._utterance_text
        if is_final and text:
            text = self._restore_punctuation(text)
        confidence_value = item.get("confidence") if isinstance(item, dict) else None
        if confidence_value is not None:
            self._utterance_confidence = min(
                1.0,
                max(0.0, float(confidence_value)),
            )
        confidence = self._utterance_confidence
        duration_ms = self._utterance_audio_bytes / (self._config.sample_rate_hz * 2) * 1000
        result = (
            TranscriptResult(text, confidence, latency_ms, duration_ms, is_final) if text else None
        )
        if is_final:
            self._clear_utterance()
        return result

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        if self._closed:
            raise AsrProviderError("session_closed", "语音会话已关闭。")
        if len(pcm_s16le) % 2:
            raise AsrProviderError(
                "invalid_audio_frame",
                "PCM 音频帧长度必须是 2 字节的整数倍。",
                recoverable=True,
            )
        self._utterance_audio_bytes += len(pcm_s16le)
        result = await asyncio.to_thread(self._generate, pcm_s16le, is_final=False)
        return (result,) if result else ()

    async def flush(self) -> Sequence[TranscriptResult]:
        if self._utterance_audio_bytes == 0 and not self._utterance_text and not self._cache:
            return ()
        result = await asyncio.to_thread(self._generate, b"", is_final=True)
        return (result,) if result else ()

    async def finish(self) -> Sequence[TranscriptResult]:
        results = await self.flush()
        self._closed = True
        return results

    async def close(self) -> None:
        self._closed = True
        self._clear_utterance()


class _WhisperModelHandle:
    def __init__(self, model_name: str, device: str) -> None:
        try:
            import whisper  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AsrProviderError(
                "provider_dependency_missing",
                "Whisper 依赖未安装，无法运行离线基线。",
            ) from exc
        try:
            self.model = whisper.load_model(model_name, device=device)
        except Exception as exc:
            raise AsrProviderError(
                "provider_initialization_failed",
                "Whisper 模型加载失败，请检查模型名称、设备和缓存。",
            ) from exc
        self.lock = threading.Lock()


_WHISPER_MODEL_LOAD_LOCK = threading.Lock()


@lru_cache(maxsize=4)
def _get_whisper_model_cached(model_name: str, device: str) -> _WhisperModelHandle:
    return _WhisperModelHandle(model_name, device)


def _get_whisper_model(model_name: str, device: str) -> _WhisperModelHandle:
    with _WHISPER_MODEL_LOAD_LOCK:
        return _get_whisper_model_cached(model_name, device)


class WhisperAdapter:
    """Offline Whisper baseline: buffers PCM and emits a final result only."""

    provider_name = "whisper"

    def __init__(self, *, model_name: str, device: str) -> None:
        self._model_name = model_name
        self._device = device
        self._config: AsrSessionConfig | None = None
        self._audio = bytearray()
        self._closed = False

    async def start(self, config: AsrSessionConfig) -> None:
        if config.sample_rate_hz != 16_000 or config.channels != 1:
            raise AsrProviderError(
                "unsupported_audio_format",
                "Whisper 基线当前仅接受 16 kHz 单声道 PCM 音频。",
                recoverable=True,
            )
        await asyncio.to_thread(_get_whisper_model, self._model_name, self._device)
        self._config = config

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        if self._closed:
            raise AsrProviderError("session_closed", "语音会话已关闭。")
        if len(pcm_s16le) % 2:
            raise AsrProviderError(
                "invalid_audio_frame",
                "PCM 音频帧长度必须是 2 字节的整数倍。",
                recoverable=True,
            )
        self._audio.extend(pcm_s16le)
        return ()

    def _transcribe(self) -> TranscriptResult:
        if self._config is None:
            raise AsrProviderError("session_not_started", "语音会话尚未开始。")
        handle = _get_whisper_model(self._model_name, self._device)
        waveform = np.frombuffer(self._audio, dtype="<i2").astype(np.float32) / 32768.0
        started = perf_counter()
        try:
            with handle.lock:
                raw = handle.model.transcribe(waveform, language="zh", fp16=self._device != "cpu")
        except Exception as exc:
            raise AsrProviderError(
                "recognition_failed",
                "Whisper 识别失败，请重试或检查音频输入。",
                recoverable=True,
            ) from exc
        latency_ms = (perf_counter() - started) * 1000
        segments = raw.get("segments", [])
        logprobs = [
            float(segment["avg_logprob"]) for segment in segments if "avg_logprob" in segment
        ]
        confidence = math.exp(sum(logprobs) / len(logprobs)) if logprobs else 0.0
        duration_ms = len(self._audio) / (self._config.sample_rate_hz * 2) * 1000
        return TranscriptResult(
            text=str(raw.get("text", "")).strip(),
            confidence=min(1.0, max(0.0, confidence)),
            latency_ms=latency_ms,
            audio_duration_ms=duration_ms,
            is_final=True,
        )

    async def flush(self) -> Sequence[TranscriptResult]:
        if not self._audio:
            return ()
        result = await asyncio.to_thread(self._transcribe)
        self._audio.clear()
        return (result,)

    async def finish(self) -> Sequence[TranscriptResult]:
        results = await self.flush()
        self._closed = True
        return results

    async def close(self) -> None:
        self._closed = True
        self._audio.clear()


def create_asr_adapter(settings: Settings) -> AsrAdapter:
    provider = settings.asr_provider.strip().lower()
    if provider == "disabled":
        return DisabledAsrAdapter()
    if provider == "funasr":
        return FunAsrAdapter(
            model_name=settings.asr_model,
            vad_model=settings.asr_vad_model,
            punc_model=settings.asr_punc_model,
            device=settings.asr_device,
        )
    if provider == "whisper":
        return WhisperAdapter(model_name=settings.asr_model, device=settings.asr_device)
    raise AsrProviderError(
        "unknown_provider",
        f"不支持的 ASR 提供方：{settings.asr_provider}",
    )
