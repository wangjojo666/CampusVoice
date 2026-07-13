from __future__ import annotations

from typing import Any

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
    assert payload["title"] == "携带校园卡参加人工智能专业考试"
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
