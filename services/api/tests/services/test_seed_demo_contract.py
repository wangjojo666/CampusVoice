from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.schemas.settings import UserSettingsUpdate
from scripts import seed_demo


class _Client:
    def get(self, path: str) -> object:
        assert path == "/api/notice-radar/series"
        return {"items": []}


def test_notice_seed_includes_migratable_campus_card_reminder(monkeypatch: Any) -> None:
    writes: list[dict[str, Any]] = []

    def fake_confirmed_write(
        client: object,
        method: str,
        path: str,
        *,
        json: object,
        headers: dict[str, str] | None = None,
    ) -> object:
        del client
        writes.append({"method": method, "path": path, "json": json, "headers": headers or {}})
        if path == "/api/notice-radar/series":
            return {"id": "series-1"}
        if path.endswith("/versions"):
            revision = json["revision_number"]  # type: ignore[index]
            return {
                "id": f"document-v{revision}",
                "claims": [
                    {
                        "id": f"start-v{revision}",
                        "chunk_id": f"start-chunk-v{revision}",
                        "claim_key": "event.start_at",
                    },
                    {
                        "id": f"materials-v{revision}",
                        "chunk_id": f"materials-chunk-v{revision}",
                        "claim_key": "required_materials",
                    },
                ],
            }
        return {"record_id": f"record-{len(writes)}"}

    monkeypatch.setattr(seed_demo, "confirmed_write", fake_confirmed_write)
    monkeypatch.setattr(seed_demo, "checked", lambda response, **_: response)

    summary = seed_demo.seed_notice_radar(_Client())  # type: ignore[arg-type]

    reminder = next(
        write
        for write in writes
        if write["headers"].get("Idempotency-Key") == "radar-ai-exam-campus-card-v1"
    )
    payload = reminder["json"]
    assert reminder["path"] == "/api/tasks"
    assert payload["title"] == "携带校园卡参加机器学习考试"
    assert payload["source_document_id"] == "document-v1"
    assert payload["source_chunk_id"] == "materials-chunk-v1"
    assert payload["source_claim_id"] == "materials-v1"
    assert payload["due_at"] == "2026-07-18T09:00:00+08:00"
    assert payload["reminder_at"] == "2026-07-18T08:00:00+08:00"
    assert summary["tasks"] == 3
    assert summary["review_tasks"] == 2
    assert summary["campus_card_reminder"] is not None

    settings_write = writes[0]
    assert settings_write["json"] == {"major": "人工智能", "grade": "2024 级"}


def test_demo_settings_payload_matches_current_update_schema() -> None:
    parsed = UserSettingsUpdate.model_validate(seed_demo.DEMO_SETTINGS_UPDATE)

    assert parsed.model_fields_set == {
        "major",
        "grade",
        "current_courses",
        "teacher_names",
        "default_reminder_minutes",
        "timezone",
    }
    assert "asr_provider" not in seed_demo.DEMO_SETTINGS_UPDATE
    assert "asr_model" not in seed_demo.DEMO_SETTINGS_UPDATE
    assert "asr_device" not in seed_demo.DEMO_SETTINGS_UPDATE


def test_checked_reports_the_real_error_response_body() -> None:
    response = httpx.Response(
        422,
        request=httpx.Request("PATCH", "http://localhost:8000/api/settings"),
        json={
            "detail": [
                {
                    "type": "extra_forbidden",
                    "loc": ["body", "asr_provider"],
                    "msg": "Extra inputs are not permitted",
                }
            ]
        },
    )

    with pytest.raises(RuntimeError, match="asr_provider"):
        seed_demo.checked(response, duplicate_ok=False)


def test_notice_seed_reuses_stable_identity_on_repeated_runs(monkeypatch: Any) -> None:
    state: dict[str, Any] = {"series": None, "records": {}, "versions": {}}

    class StatefulClient:
        def get(self, path: str) -> object:
            assert path == "/api/notice-radar/series"
            return {"items": [state["series"]] if state["series"] else []}

    def fake_confirmed_write(
        client: object,
        method: str,
        path: str,
        *,
        json: object,
        headers: dict[str, str] | None = None,
    ) -> object:
        del client, method
        body = json  # type: ignore[assignment]
        if path == "/api/settings":
            return {"success": True}
        if path == "/api/notice-radar/series":
            state["series"] = {
                "id": "series-1",
                "canonical_key": body["canonical_key"],  # type: ignore[index]
            }
            return state["series"]
        if path.endswith("/versions"):
            revision = body["revision_number"]  # type: ignore[index]
            version = state["versions"].setdefault(
                revision,
                {
                    "id": f"document-v{revision}",
                    "claims": [
                        {
                            "id": f"start-v{revision}",
                            "chunk_id": f"start-chunk-v{revision}",
                            "claim_key": "event.start_at",
                        },
                        {
                            "id": f"materials-v{revision}",
                            "chunk_id": f"materials-chunk-v{revision}",
                            "claim_key": "required_materials",
                        },
                    ],
                },
            )
            return version
        key = (headers or {}).get("Idempotency-Key")
        assert key
        return state["records"].setdefault(key, {"record_id": f"record-{key}"})

    monkeypatch.setattr(seed_demo, "confirmed_write", fake_confirmed_write)
    monkeypatch.setattr(seed_demo, "checked", lambda response, **_: response)
    client = StatefulClient()

    first = seed_demo.seed_notice_radar(client)  # type: ignore[arg-type]
    second = seed_demo.seed_notice_radar(client)  # type: ignore[arg-type]

    assert first == second
    assert set(state["versions"]) == {1, 2}
    assert set(state["records"]) == {
        "radar-ai-exam-event-v1",
        "radar-ai-exam-review-1-v1",
        "radar-ai-exam-review-2-v1",
        "radar-ai-exam-campus-card-v1",
    }
