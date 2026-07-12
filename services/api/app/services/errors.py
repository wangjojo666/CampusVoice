from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(DomainError):
    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(
            "not_found",
            f"{entity} was not found",
            status_code=404,
            details={"entity": entity, "id": entity_id},
        )


class ConflictError(DomainError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, status_code=409, details=details)


class ConfirmationRequiredError(DomainError):
    def __init__(self, action: dict[str, Any]) -> None:
        super().__init__(
            "confirmation_required",
            "This operation must complete the confirmation workflow before execution",
            status_code=428,
            details={"pending_action": action},
        )


class VerificationFailedError(DomainError):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__(
            "verification_failed",
            "The database change could not be verified and was not reported as successful",
            status_code=500,
            details=details,
        )
