from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AsrEventType = Literal[
    "ready",
    "speech_start",
    "interim",
    "final",
    "speech_end",
    "pong",
    "error",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AsrStartMessage(_StrictModel):
    type: Literal["start"] = "start"
    sample_rate_hz: Literal[16000] = 16000
    channels: Literal[1] = 1
    sample_width_bytes: Literal[2] = 2
    language: Literal["zh"] = "zh"
    hotwords: list[str] = Field(default_factory=list, max_length=500)


class AsrStopMessage(_StrictModel):
    type: Literal["stop"] = "stop"


class AsrFlushMessage(_StrictModel):
    type: Literal["flush"] = "flush"


class AsrPingMessage(_StrictModel):
    type: Literal["ping"] = "ping"


AsrClientMessage = Annotated[
    AsrStartMessage | AsrStopMessage | AsrFlushMessage | AsrPingMessage,
    Field(discriminator="type"),
]


class AsrServerEvent(_StrictModel):
    type: AsrEventType
    session_id: str
    sequence: int = Field(ge=0)
    provider: str | None = None
    transcription_id: str | None = None
    protocol_version: Literal[1] = 1
    text: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    latency_ms: float | None = Field(default=None, ge=0)
    audio_duration_ms: float | None = Field(default=None, ge=0)
    code: str | None = None
    message: str | None = None
    recoverable: bool | None = None

    @model_validator(mode="after")
    def validate_event_payload(self) -> "AsrServerEvent":
        if self.type in {"interim", "final"} and (
            self.text is None or self.confidence is None or self.latency_ms is None
        ):
            raise ValueError("transcript events require text, confidence, and latency_ms")
        if self.type == "error" and (not self.code or not self.message):
            raise ValueError("error events require code and message")
        return self
