from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.services.asr import AsrSessionConfig, TranscriptResult
from tests.helpers import confirm_action, confirmed_write


class LineageAsrAdapter:
    provider_name = "test-lineage"

    def __init__(self) -> None:
        self.started_with: AsrSessionConfig | None = None

    async def start(self, config: AsrSessionConfig) -> None:
        self.started_with = config

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        del pcm_s16le
        return ()

    async def flush(self) -> Sequence[TranscriptResult]:
        return ()

    async def finish(self) -> Sequence[TranscriptResult]:
        return (
            TranscriptResult(
                text="复习机气学习重点",
                confidence=0.91,
                latency_ms=32.0,
                audio_duration_ms=640.0,
                is_final=True,
            ),
        )

    async def close(self) -> None:
        return None


def test_settings_hotwords_and_voice_lineage_reach_action_log(client: TestClient) -> None:
    updated = confirmed_write(
        client,
        "PATCH",
        "/api/settings",
        {
            "current_courses": [
                {"code": "AI301", "name": "机器学习", "teacher": "张老师"},
            ],
            "teacher_names": ["张老师", "李老师"],
        },
    )
    assert updated.status_code == 200, updated.text

    adapter = LineageAsrAdapter()
    client.app.state.asr_adapter_factory = lambda: adapter
    origin = "http://localhost:3000"
    ticket_response = client.post("/api/auth/ws-ticket", headers={"Origin": origin})
    assert ticket_response.status_code == 200, ticket_response.text
    ticket = ticket_response.json()["ticket"]
    with client.websocket_connect(
        "/ws/asr",
        headers={"Origin": origin},
        subprotocols=["campusvoice", f"campusvoice.ticket.{ticket}"],
    ) as websocket:
        ready = websocket.receive_json()
        websocket.send_json({"type": "start", "hotwords": ["自定义"]})
        websocket.send_json({"type": "stop"})
        final = websocket.receive_json()
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    assert ready["type"] == "ready"
    assert final["type"] == "final"
    assert final["session_id"] == ready["session_id"]
    assert final["transcription_id"].startswith("trn_")
    assert adapter.started_with is not None
    assert adapter.started_with.hotwords == (
        "机器学习",
        "AI301",
        "张老师",
        "李老师",
        "自定义",
    )

    correction = client.post(
        "/api/correction/preview",
        json={
            "transcription_id": final["transcription_id"],
            "text": final["text"],
            "asr_confidence": final["confidence"],
            "terms": [
                {
                    "term": "机器学习",
                    "source": "ai_term",
                    "aliases": ["机气学习"],
                    "context_keywords": ["复习"],
                }
            ],
        },
    )
    assert correction.status_code == 200, correction.text
    correction_id = correction.json()["record"]["id"]
    corrected_text = "复习机器学习重点"
    decision = client.post(
        f"/api/correction/{correction_id}/decision",
        json={"corrected_text": corrected_text, "confirmed": True},
    )
    assert decision.status_code == 200, decision.text

    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": {"title": corrected_text, "source_type": "voice"},
            "asr_confidence": final["confidence"],
            "source_text": final["text"],
            "corrected_text": corrected_text,
            "voice_session_id": ready["session_id"],
            "transcription_id": final["transcription_id"],
        },
    )
    assert prepared.status_code == 201, prepared.text
    action_id = prepared.json()["id"]
    confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["success"] is True

    logs = client.get("/api/action-logs").json()
    assert logs["total"] == 1
    log = logs["items"][0]
    assert log["voice_session_id"] == ready["session_id"]
    assert log["transcription_id"] == final["transcription_id"]
    assert log["source_text"] == "复习机气学习重点"
    assert log["corrected_text"] == "复习机器学习重点"

    mismatch = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": {"title": "不应创建"},
            "source_text": final["text"],
            "voice_session_id": "another-session",
            "transcription_id": final["transcription_id"],
        },
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "voice_source_mismatch"

    undone = client.post(f"/api/actions/{action_id}/undo")
    assert undone.status_code == 200
    assert undone.json()["success"] is True
    updated_log = client.get("/api/action-logs").json()["items"][0]
    assert updated_log["verification_result"]["undone"] is True
