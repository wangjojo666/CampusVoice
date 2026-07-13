"""Load synthetic CampusVoice demo settings, records, hotwords, and notices via REST."""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DOCUMENTS = ROOT / "data" / "sample-documents"


def checked(response: httpx.Response, *, duplicate_ok: bool = True) -> dict[str, Any]:
    if duplicate_ok and response.status_code == 409:
        return {"status": "already_exists", "detail": response.json()}
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"items": payload}


def confirmed_write(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json: object,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    issued = checked(
        client.post(
            "/api/auth/write-challenges",
            json={"method": method.upper(), "path": path, "body": json},
        ),
        duplicate_ok=False,
    )
    request_headers = dict(headers or {})
    request_headers["X-Write-Challenge"] = str(issued["challenge"])
    return client.request(method, path, headers=request_headers, json=json)


def seed_notice_radar(client: httpx.Client) -> dict[str, Any]:
    """Create the repeatable v1 -> v2 -> impact demo without real student data."""

    checked(
        confirmed_write(
            client,
            "PATCH",
            "/api/settings",
            json={"major": "人工智能", "grade": "2024 级"},
        ),
        duplicate_ok=False,
    )
    series_rows = checked(client.get("/api/notice-radar/series"), duplicate_ok=False)["items"]
    series = next(
        (item for item in series_rows if item["canonical_key"] == "ai-exam-2026"),
        None,
    )
    if series is None:
        series = checked(
            confirmed_write(
                client,
                "POST",
                "/api/notice-radar/series",
                json={
                    "canonical_key": "ai-exam-2026",
                    "title": "2026 人工智能专业考试安排",
                    "department": "计算机学院教务办公室",
                    "source_key": "synthetic-demo/ai-exam",
                },
            ),
            duplicate_ok=False,
        )

    v1 = checked(
        confirmed_write(
            client,
            "POST",
            f"/api/notice-radar/series/{series['id']}/versions",
            json={
                "title": "2026 人工智能专业考试安排",
                "content": (
                    "适用于 2024 级人工智能专业。\n"
                    "考试时间：2026-07-18 09:00–11:00。\n"
                    "地点：教学楼 A302。\n"
                    "要求携带校园卡。提前 1440 分钟提醒。"
                ),
                "revision_number": 1,
                "version_label": "v1",
                "supersedes_document_id": None,
                "department": "计算机学院教务办公室",
                "publish_date": "2026-07-01",
                "applicable_group": "2024 级人工智能专业",
                "ingest_source": "seed",
            },
        ),
        duplicate_ok=False,
    )
    start_claim = next(item for item in v1["claims"] if item["claim_key"] == "event.start_at")
    materials_claim = next(
        item for item in v1["claims"] if item["claim_key"] == "required_materials"
    )
    lineage = {
        "source_type": "document",
        "source_document_id": v1["id"],
        "source_chunk_id": start_claim["chunk_id"],
        "source_claim_id": start_claim["id"],
    }
    event = checked(
        confirmed_write(
            client,
            "POST",
            "/api/events",
            headers={"Idempotency-Key": "radar-ai-exam-event-v1"},
            json={
                "title": "人工智能专业考试",
                "start_at": "2026-07-18T09:00:00+08:00",
                "end_at": "2026-07-18T11:00:00+08:00",
                "location": "教学楼 A302",
                "reminder_minutes": 1440,
                **lineage,
            },
        )
    )
    task_results = []
    for index, (title, due_at) in enumerate(
        (
            ("完成考试知识点复习", "2026-07-17T20:00:00+08:00"),
            ("完成模拟题复盘", "2026-07-18T08:00:00+08:00"),
        ),
        start=1,
    ):
        task_results.append(
            checked(
                confirmed_write(
                    client,
                    "POST",
                    "/api/tasks",
                    headers={"Idempotency-Key": f"radar-ai-exam-review-{index}-v1"},
                    json={
                        "title": title,
                        "due_at": due_at,
                        "reminder_at": "2026-07-17T08:00:00+08:00",
                        "priority": "high",
                        **lineage,
                    },
                )
            )
        )

    campus_card_reminder = checked(
        confirmed_write(
            client,
            "POST",
            "/api/tasks",
            headers={"Idempotency-Key": "radar-ai-exam-campus-card-v1"},
            json={
                "title": "携带校园卡参加人工智能专业考试",
                "description": "考试材料提醒：请在入场时携带校园卡。",
                "due_at": "2026-07-18T09:00:00+08:00",
                "reminder_at": "2026-07-18T08:00:00+08:00",
                "priority": "high",
                "source_type": "document",
                "source_document_id": v1["id"],
                "source_chunk_id": materials_claim["chunk_id"],
                "source_claim_id": materials_claim["id"],
            },
        )
    )

    v2 = checked(
        confirmed_write(
            client,
            "POST",
            f"/api/notice-radar/series/{series['id']}/versions",
            json={
                "title": "2026 人工智能专业考试安排",
                "content": (
                    "适用于 2024 级人工智能专业同学。\n"
                    "考试时间：2026-07-18 14:00–16:00。\n"
                    "地点改为：教学楼 B205。\n"
                    "请按时参加，要求携带校园卡。提前 1440 分钟提醒。"
                ),
                "revision_number": 2,
                "version_label": "v2",
                "supersedes_document_id": v1["id"],
                "department": "计算机学院教务办公室",
                "publish_date": "2026-07-13",
                "applicable_group": "2024 级人工智能专业",
                "ingest_source": "seed",
            },
        ),
        duplicate_ok=False,
    )
    return {
        "series": series["id"],
        "v1": v1["id"],
        "v2": v2["id"],
        "event": event.get("record_id", event.get("status")),
        "tasks": len(task_results) + 1,
        "review_tasks": len(task_results),
        "campus_card_reminder": campus_card_reminder.get(
            "record_id", campus_card_reminder.get("status")
        ),
    }


def seed(base_url: str) -> None:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=90) as client:
        checked(client.get("/api/health"), duplicate_ok=False)

        settings = checked(
            confirmed_write(
                client,
                "PATCH",
                "/api/settings",
                json={
                    "major": "人工智能",
                    "grade": "2024 级",
                    "current_courses": [
                        {
                            "code": "AI2401",
                            "name": "机器学习",
                            "teacher": "林知远",
                        },
                        {
                            "code": "AI2402",
                            "name": "自然语言处理",
                            "teacher": "周明澜",
                        },
                    ],
                    "teacher_names": ["林知远", "周明澜"],
                    "default_reminder_minutes": 1440,
                    "timezone": "Asia/Shanghai",
                    "asr_provider": "funasr",
                    "asr_model": "paraformer-zh-streaming",
                    "asr_device": "cuda:0",
                },
            ),
            duplicate_ok=False,
        )

        task = checked(
            confirmed_write(
                client,
                "POST",
                "/api/tasks",
                headers={"Idempotency-Key": "demo-task-nlp-report-v1"},
                json={
                    "title": "提交自然语言处理课程设计报告",
                    "course": "自然语言处理",
                    "due_at": "2026-07-20T15:59:00Z",
                    "reminder_at": "2026-07-19T15:59:00Z",
                    "priority": "high",
                    "source_type": "manual",
                },
            )
        )

        event = checked(
            confirmed_write(
                client,
                "POST",
                "/api/events",
                headers={"Idempotency-Key": "demo-event-machine-learning-exam-v1"},
                json={
                    "title": "机器学习期末考试",
                    "course": "机器学习",
                    "start_at": "2026-07-18T01:00:00Z",
                    "end_at": "2026-07-18T03:00:00Z",
                    "location": "教学楼A302",
                    "reminder_minutes": 1440,
                    "source_type": "manual",
                },
            )
        )

        hotword_results = []
        for term, category in [
            ("机器学习", "course"),
            ("自然语言处理", "course"),
            ("检索增强生成", "ai_term"),
            ("林知远", "teacher"),
        ]:
            hotword_results.append(
                checked(
                    confirmed_write(
                        client,
                        "POST",
                        "/api/hotwords",
                        json={
                            "term": term,
                            "category": category,
                            "source": "demo",
                            "weight": 2,
                        },
                    )
                )
            )

        document_results = []
        metadata_by_name = {
            "2026-ai-exam-notice.md": {
                "title": "人工智能专业考试安排通知",
                "department": "计算机学院教务办公室",
                "publish_date": "2026-07-01",
                "applicable_group": "2024级人工智能专业本科生",
                "version": "1.0",
            },
            "2026-registration-notice.txt": {
                "title": "人工智能创新竞赛报名通知",
                "department": "创新创业学院",
                "publish_date": "2026-06-28",
                "applicable_group": "全校本科生",
                "version": "1.0",
            },
        }
        for path in sorted(SAMPLE_DOCUMENTS.iterdir()):
            if not path.is_file() or path.name not in metadata_by_name:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as source:
                response = client.post(
                    "/api/documents",
                    data=metadata_by_name[path.name],
                    files={"file": (path.name, source, mime)},
                )
            document_results.append(checked(response))

        radar = seed_notice_radar(client)

    print(
        "Demo seed complete:",
        {
            "settings": bool(settings),
            "task": task.get("record_id", task.get("status")),
            "event": event.get("record_id", event.get("status")),
            "hotwords": len(hotword_results),
            "documents": len(document_results),
            "notice_radar": radar,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    seed(args.base_url)


if __name__ == "__main__":
    main()
