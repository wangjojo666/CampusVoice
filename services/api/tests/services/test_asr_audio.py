import asyncio
from typing import Any

import pytest

from app.services.asr import audio
from app.services.asr.audio import AudioDecodeError, decode_mp3_to_pcm_s16le


class FakeProcess:
    def __init__(self, pcm: bytes, *, returncode: int = 0) -> None:
        self.pcm = pcm
        self.returncode = returncode
        self.input_audio: bytes | None = None
        self.killed = False
        self.wait_calls = 0

    async def communicate(self, encoded_audio: bytes) -> tuple[bytes, bytes]:
        self.input_audio = encoded_audio
        return self.pcm, b"suppressed diagnostic"

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode


@pytest.mark.asyncio
async def test_mp3_decoder_uses_fixed_non_shell_command(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(b"\x00\x00" * 160)
    captured: dict[str, Any] = {}

    async def create_process(*args: object, **kwargs: object) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)

    pcm = await decode_mp3_to_pcm_s16le(b"bounded-mp3", 0.01)

    assert pcm == b"\x00\x00" * 160
    assert process.input_audio == b"bounded-mp3"
    args = captured["args"]
    assert args[0] == "ffmpeg"
    mp3_index = args.index("mp3")
    assert args[mp3_index - 1] == "-f"
    assert "pipe:0" in args
    assert "pipe:1" in args
    assert captured["kwargs"] == {
        "stdin": audio.PIPE,
        "stdout": audio.PIPE,
        "stderr": audio.DEVNULL,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pcm", "returncode", "expected_code"),
    [
        (b"", 0, "invalid_decoded_audio"),
        (b"\x00", 0, "invalid_decoded_audio"),
        (b"\x00\x00" * 161, 0, "audio_duration_exceeded"),
        (b"", 1, "invalid_encoded_audio"),
    ],
)
async def test_mp3_decoder_rejects_invalid_or_oversized_output(
    monkeypatch: pytest.MonkeyPatch,
    pcm: bytes,
    returncode: int,
    expected_code: str,
) -> None:
    process = FakeProcess(pcm, returncode=returncode)

    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(AudioDecodeError) as raised:
        await decode_mp3_to_pcm_s16le(b"bounded-mp3", 0.01)

    assert raised.value.code == expected_code


@pytest.mark.asyncio
async def test_mp3_decoder_fails_closed_when_ffmpeg_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(AudioDecodeError) as raised:
        await decode_mp3_to_pcm_s16le(b"bounded-mp3", 1.0)

    assert raised.value.code == "audio_decoder_unavailable"


@pytest.mark.asyncio
async def test_mp3_decoder_cancellation_reaps_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    never_complete = asyncio.Event()

    class BlockingProcess(FakeProcess):
        async def communicate(self, encoded_audio: bytes) -> tuple[bytes, bytes]:
            self.input_audio = encoded_audio
            started.set()
            await never_complete.wait()
            raise AssertionError("unreachable")

    process = BlockingProcess(b"")

    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)
    task = asyncio.create_task(decode_mp3_to_pcm_s16le(b"bounded-mp3", 1.0))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
    assert process.wait_calls == 1


@pytest.mark.asyncio
async def test_mp3_decoder_timeout_kills_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingProcess(FakeProcess):
        async def communicate(self, encoded_audio: bytes) -> tuple[bytes, bytes]:
            self.input_audio = encoded_audio
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    process = BlockingProcess(b"")

    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)
    with pytest.raises(AudioDecodeError) as raised:
        await decode_mp3_to_pcm_s16le(
            b"bounded-mp3",
            1.0,
            timeout_seconds=0.001,
        )

    assert raised.value.code == "audio_decode_timeout"
    assert process.killed is True
    assert process.wait_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_mp3_decoder_reap_is_bounded_when_wait_never_returns(
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    started = asyncio.Event()

    class UnreapableProcess(FakeProcess):
        async def communicate(self, encoded_audio: bytes) -> tuple[bytes, bytes]:
            self.input_audio = encoded_audio
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def wait(self) -> int:
            self.wait_calls += 1
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    process = UnreapableProcess(b"")

    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(audio.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(audio, "_PROCESS_REAP_TIMEOUT_SECONDS", 0.001)
    task = asyncio.create_task(
        decode_mp3_to_pcm_s16le(
            b"bounded-mp3",
            1.0,
            timeout_seconds=0.001 if not cancelled else 60.0,
        )
    )
    await started.wait()
    if cancelled:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.1)
    else:
        with pytest.raises(AudioDecodeError) as raised:
            await asyncio.wait_for(task, timeout=0.1)
        assert raised.value.code == "audio_decode_timeout"

    assert process.killed is True
    assert process.wait_calls == 1
