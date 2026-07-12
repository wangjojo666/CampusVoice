from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import UserSettings
from app.repositories.settings import UserSettingsRepository
from app.schemas.settings import (
    CoursePreference,
    UserSettingsMutationResponse,
    UserSettingsUpdate,
    UserSettingsView,
)
from app.services.errors import ConfirmationRequiredError, NotFoundError, VerificationFailedError


class UserSettingsService:
    def __init__(self) -> None:
        self.repository = UserSettingsRepository()

    async def get(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> UserSettingsView:
        entity = await self.repository.get(session, user_id)
        if entity is None:
            raise NotFoundError("user_settings", user_id)
        return _to_view(entity)

    async def update(
        self,
        session: AsyncSession,
        user_id: str,
        data: UserSettingsUpdate,
        *,
        confirmed: bool,
    ) -> UserSettingsMutationResponse:
        if not confirmed:
            raise ConfirmationRequiredError(
                {"operation": "update_settings", "required_confirmations": 1}
            )
        expected: dict[str, Any] = {}
        async with session.begin():
            entity = await self.repository.get(session, user_id, lock=True)
            if entity is None:
                raise NotFoundError("user_settings", user_id)
            fields = data.model_fields_set
            for name in {"major", "grade", "default_reminder_minutes", "timezone"} & fields:
                value = getattr(data, name)
                setattr(entity, name, value)
                expected[name] = value
            if "current_courses" in fields:
                courses = data.current_courses or []
                entity.current_courses = [course.model_dump(mode="json") for course in courses]
                expected["current_courses"] = entity.current_courses
            if "teacher_names" in fields:
                entity.teacher_names = list(data.teacher_names or [])
                expected["teacher_names"] = entity.teacher_names
            asr = dict(entity.asr_model_config)
            for request_name, storage_name in {
                "asr_provider": "provider",
                "asr_model": "model",
                "asr_device": "device",
            }.items():
                if request_name in fields:
                    value = getattr(data, request_name)
                    asr[storage_name] = value
                    expected[request_name] = value
            entity.asr_model_config = asr

        session.expire_all()
        verified_entity = await self.repository.get(session, user_id)
        if verified_entity is None:
            await session.rollback()
            raise VerificationFailedError({"reason": "settings row disappeared after commit"})
        verified_view = _to_view(verified_entity)
        actual = verified_view.model_dump(mode="json")
        verified_fields = {
            name: _normalize_expected(value) == actual.get(name) for name, value in expected.items()
        }
        await session.rollback()
        if not all(verified_fields.values()):
            raise VerificationFailedError(
                {"verified_fields": verified_fields, "reason": "settings fields differ"}
            )
        return UserSettingsMutationResponse(
            success=True,
            verified_fields=verified_fields,
            message="设置已更新并通过数据库验证",
            settings=verified_view,
        )


def _to_view(entity: UserSettings) -> UserSettingsView:
    asr = entity.asr_model_config
    return UserSettingsView(
        major=entity.major,
        grade=entity.grade,
        current_courses=[CoursePreference.model_validate(item) for item in entity.current_courses],
        teacher_names=entity.teacher_names,
        default_reminder_minutes=entity.default_reminder_minutes,
        timezone=entity.timezone,
        asr_provider=str(asr.get("provider", "disabled")),
        asr_model=str(asr.get("model", "paraformer-zh-streaming")),
        asr_device=str(asr.get("device", "cpu")),
        updated_at=entity.updated_at,
    )


def _normalize_expected(value: Any) -> Any:
    if isinstance(value, list):
        return [
            item.model_dump(mode="json") if isinstance(item, CoursePreference) else item
            for item in value
        ]
    return value
