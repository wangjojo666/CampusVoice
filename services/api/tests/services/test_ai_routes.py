import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_intent_document_search_and_correction_routes_use_real_services() -> None:
    app = create_app(
        Settings(
            env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            database_auto_create=True,
            asr_provider="disabled",
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            intent = await client.post(
                "/api/intent/parse",
                json={"text": "把机器学习考试加到日历，7月18日上午九点"},
            )
            assert intent.status_code == 200
            assert intent.json()["intent"] == "create_event"
            assert intent.json()["requires_confirmation"] is True

            upload = await client.post(
                "/api/documents",
                files={
                    "file": (
                        "notice.txt",
                        "机器学习考试地点为教学楼A302。".encode(),
                        "text/plain",
                    )
                },
                data={
                    "title": "考试通知",
                    "publish_date": "2026-07-01",
                    "version": "v1",
                },
            )
            assert upload.status_code == 201, upload.text
            assert upload.json()["chunk_count"] == 1

            search = await client.post(
                "/api/knowledge/search",
                json={"query": "机器学习考试地点", "min_similarity": 0.05},
            )
            assert search.status_code == 200
            assert search.json()["results"][0]["file_title"] == "考试通知"
            assert search.json()["results"][0]["page_number"] is None

            correction = await client.post(
                "/api/correction/preview",
                json={
                    "text": "复习机气学习重点",
                    "asr_confidence": 0.1,
                    "terms": [
                        {
                            "term": "机器学习",
                            "aliases": ["机气学习"],
                            "source": "ai_term",
                            "context_keywords": ["复习"],
                        }
                    ],
                    "document_terms": ["机器学习"],
                    "recent_context": ["机器学习复习资料"],
                },
            )
            assert correction.status_code == 200
            assert correction.json()["record"]["corrected_text"] == "复习机器学习重点"
            correction_record_id = correction.json()["record"]["id"]
            decision = await client.post(
                f"/api/correction/{correction_record_id}/decision",
                json={"corrected_text": "复习机器学习重点", "confirmed": True},
            )
            assert decision.status_code == 200
            assert decision.json() == {
                "id": correction_record_id,
                "corrected_text": "复习机器学习重点",
                "user_confirmed": True,
            }
