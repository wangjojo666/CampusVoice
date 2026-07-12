from fastapi import APIRouter

from app.api.routes import (
    action_logs,
    actions,
    correction,
    documents,
    events,
    health,
    hotwords,
    intent,
    knowledge,
    settings,
    tasks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(tasks.router)
api_router.include_router(events.router)
api_router.include_router(hotwords.router)
api_router.include_router(actions.router)
api_router.include_router(action_logs.router)
api_router.include_router(settings.router)
api_router.include_router(intent.router)
api_router.include_router(correction.router)
api_router.include_router(documents.router)
api_router.include_router(knowledge.router)
