import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any

from app.models.entities import PendingAction
from app.services.errors import ConflictError


def action_payload_hash(action: PendingAction) -> str:
    frozen = {
        "action_type": action.action_type.value,
        "target_id": action.target_id,
        "payload": action.payload,
        "execution_options": action.execution_options,
        "risk_level": action.risk_level.value,
        "required_confirmations": action.required_confirmations,
    }
    serialized = json.dumps(
        frozen,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class VerifiedChallenge:
    nonce_hash: str
    stage: int
    payload_hash: str
    expires_at: datetime


class ConfirmationChallengeService:
    def __init__(self, secret: str, *, ttl_seconds: int = 120) -> None:
        if len(secret) < 32:
            raise ValueError("confirmation secret must contain at least 32 characters")
        self._secret = secret.encode()
        self._ttl_seconds = ttl_seconds

    def issue(self, action: PendingAction, user_id: str) -> tuple[str, int, datetime]:
        stage = action.confirmations_received + 1
        if stage > action.required_confirmations:
            raise ConflictError(
                "confirmation_complete", "The action already has every confirmation"
            )
        expires_at = min(
            action.expires_at,
            datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
        )
        claims: dict[str, Any] = {
            "v": 1,
            "user_id": user_id,
            "action_id": action.id,
            "payload_hash": action_payload_hash(action),
            "stage": stage,
            "exp": int(expires_at.timestamp()),
            "nonce": token_urlsafe(32),
        }
        encoded = _encode(
            json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        signature = _encode(hmac.digest(self._secret, encoded.encode(), "sha256"))
        return f"{encoded}.{signature}", stage, expires_at

    def verify(
        self,
        challenge: str,
        *,
        action: PendingAction,
        user_id: str,
    ) -> VerifiedChallenge:
        try:
            encoded, supplied_signature = challenge.split(".", maxsplit=1)
            expected_signature = _encode(hmac.digest(self._secret, encoded.encode(), "sha256"))
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError("signature mismatch")
            claims = json.loads(_decode(encoded))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConflictError(
                "invalid_confirmation_challenge",
                "The confirmation challenge is invalid",
            ) from exc
        now = datetime.now(UTC)
        expires_at = datetime.fromtimestamp(int(claims.get("exp", 0)), tz=UTC)
        expected_stage = action.confirmations_received + 1
        expected_payload_hash = action_payload_hash(action)
        if expires_at <= now:
            raise ConflictError(
                "confirmation_challenge_expired",
                "The confirmation challenge has expired",
            )
        checks = {
            "user_id": user_id,
            "action_id": action.id,
            "payload_hash": expected_payload_hash,
            "stage": expected_stage,
            "v": 1,
        }
        if any(claims.get(key) != value for key, value in checks.items()):
            raise ConflictError(
                "confirmation_challenge_mismatch",
                "The confirmation challenge does not match this user, action, stage, or payload",
            )
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or len(nonce) < 32:
            raise ConflictError(
                "invalid_confirmation_challenge",
                "The confirmation challenge nonce is invalid",
            )
        return VerifiedChallenge(
            nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
            stage=expected_stage,
            payload_hash=expected_payload_hash,
            expires_at=expires_at,
        )
