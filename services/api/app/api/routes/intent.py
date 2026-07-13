from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import (
    SessionDependency,
    SettingsDependency,
    UserIdDependency,
)
from app.schemas.intent import IntentParseRequest, IntentResult
from app.services.intent import (
    ConversationService,
    IntentParseError,
    IntentParser,
    build_intent_parser,
)

router = APIRouter(prefix="/intent", tags=["intent"])


def get_intent_parser(request: Request, settings: SettingsDependency) -> IntentParser:
    return build_intent_parser(settings, metrics=request.app.state.metrics)


@router.post("/parse", response_model=IntentResult)
async def parse_intent(
    request: IntentParseRequest,
    session: SessionDependency,
    user_id: UserIdDependency,
    parser: Annotated[IntentParser, Depends(get_intent_parser)],
) -> IntentResult:
    conversations = ConversationService()
    async with session.begin():
        persisted_context = await conversations.context_for(
            session,
            user_id,
            request.conversation_id,
        )
    try:
        result = await parser.parse(
            request.text,
            context=[*persisted_context, *request.context],
            asr_confidence=request.asr_confidence,
        )
    except IntentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    async with session.begin():
        conversation_id = await conversations.record(
            session,
            user_id,
            request.conversation_id,
            result,
        )
    return result.model_copy(update={"conversation_id": conversation_id})
