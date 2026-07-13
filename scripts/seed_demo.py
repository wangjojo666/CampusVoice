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
                    "grade": "2024级",
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

    print(
        "Demo seed complete:",
        {
            "settings": bool(settings),
            "task": task.get("record_id", task.get("status")),
            "event": event.get("record_id", event.get("status")),
            "hotwords": len(hotword_results),
            "documents": len(document_results),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    seed(args.base_url)


if __name__ == "__main__":
    main()
