import asyncio
from asyncio.subprocess import DEVNULL, PIPE
from contextlib import suppress

_PROCESS_REAP_TIMEOUT_SECONDS = 1.0


class AudioDecodeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Best-effort terminate a decoder without extending the request deadline forever."""

    with suppress(ProcessLookupError):
        process.kill()
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=_PROCESS_REAP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        # The OS/asyncio child watcher remains responsible for the already-killed child.
        return


async def decode_mp3_to_pcm_s16le(
    encoded_audio: bytes,
    max_duration_seconds: float,
    *,
    timeout_seconds: float = 20.0,
    ffmpeg_binary: str = "ffmpeg",
) -> bytes:
    """Decode one bounded MP3 recording with a fixed, non-shell ffmpeg command."""

    if not encoded_audio:
        raise AudioDecodeError("empty_audio", "录音未包含可识别的音频数据。")

    max_pcm_bytes = int(max_duration_seconds * 16_000 * 2)
    decode_duration = max_duration_seconds + 0.1
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-probesize",
            "32768",
            "-analyzeduration",
            "1000000",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-t",
            f"{decode_duration:.3f}",
            "-f",
            "s16le",
            "pipe:1",
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
        )
    except OSError as exc:
        raise AudioDecodeError(
            "audio_decoder_unavailable",
            "服务端音频解码器不可用。",
        ) from exc

    try:
        pcm, _stderr = await asyncio.wait_for(
            process.communicate(encoded_audio),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        await _kill_and_reap(process)
        raise AudioDecodeError(
            "audio_decode_timeout",
            "录音解码超时，请缩短录音后重试。",
        ) from exc
    except asyncio.CancelledError:
        await _kill_and_reap(process)
        raise

    if process.returncode != 0:
        raise AudioDecodeError("invalid_encoded_audio", "录音格式无效或数据不完整。")
    if not pcm or len(pcm) % 2:
        raise AudioDecodeError("invalid_decoded_audio", "录音解码结果无效。")
    if len(pcm) > max_pcm_bytes:
        raise AudioDecodeError(
            "audio_duration_exceeded",
            "本次会话累计音频时长超过服务端限制。",
        )
    return pcm
