import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from secrets import token_urlsafe
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import utc_now
from app.models.entities import WriteChallenge
from app.services.errors import ConflictError, DomainError

_RESOURCE_SEGMENT = re.compile(r"^[^/?#]+$")


@dataclass(frozen=True, slots=True)
class IssuedWriteChallenge:
    challenge: str
    stage: int
    required_stages: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _WriteBinding:
    method: str
    path: str
    body_hash: str
    required_stages: int


def canonical_body_hash(body: Any) -> str:
    try:
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise DomainError(
            "invalid_write_challenge_body",
            "The write challenge body must be valid JSON",
            status_code=422,
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _token_hash(challenge: str) -> str:
    return hashlib.sha256(challenge.encode()).hexdigest()


def _required_stages(method: str, path: str, api_prefix: str) -> int:
    prefix = api_prefix.rstrip("/")
    if not path.startswith("/") or "?" in path or "#" in path:
        raise _unsupported_write()
    if prefix:
        if not path.startswith(f"{prefix}/"):
            raise _unsupported_write()
        relative = path[len(prefix) :]
    else:
        relative = path

    parts = relative.split("/")[1:]
    if method == "POST" and parts in (["tasks"], ["events"], ["hotwords"]):
        return 1
    if method == "PATCH" and parts == ["settings"]:
        return 1
    if (
        method == "PATCH"
        and len(parts) == 2
        and parts[0] in {"tasks", "events"}
        and _RESOURCE_SEGMENT.fullmatch(parts[1])
    ):
        return 1
    if (
        method == "DELETE"
        and len(parts) == 2
        and parts[0] == "hotwords"
        and _RESOURCE_SEGMENT.fullmatch(parts[1])
    ):
        return 2
    raise _unsupported_write()


def _unsupported_write() -> DomainError:
    return DomainError(
        "unsupported_write_challenge_target",
        "The requested method and path do not support generic write challenges",
        status_code=422,
    )


def _binding(
    *,
    method: str,
    path: str,
    body: Any,
    api_prefix: str,
) -> _WriteBinding:
    normalized_method = method.upper()
    return _WriteBinding(
        method=normalized_method,
        path=path,
        body_hash=canonical_body_hash(body),
        required_stages=_required_stages(normalized_method, path, api_prefix),
    )


def _new_record(
    *,
    raw_challenge: str,
    flow_id: str,
    user_id: str,
    binding: _WriteBinding,
    stage: int,
    expires_at: datetime,
) -> WriteChallenge:
    return WriteChallenge(
        token_hash=_token_hash(raw_challenge),
        flow_id=flow_id,
        user_id=user_id,
        method=binding.method,
        path=binding.path,
        body_hash=binding.body_hash,
        stage=stage,
        required_stages=binding.required_stages,
        expires_at=expires_at,
        created_at=utc_now(),
    )


async def issue_write_challenge(
    session: AsyncSession,
    *,
    user_id: str,
    method: str,
    path: str,
    body: Any,
    api_prefix: str,
    ttl_seconds: int,
) -> IssuedWriteChallenge:
    binding = _binding(method=method, path=path, body=body, api_prefix=api_prefix)
    raw_challenge = token_urlsafe(32)
    flow_id = f"wcf_{token_urlsafe(24)}"
    expires_at = utc_now() + timedelta(seconds=ttl_seconds)
    async with session.begin():
        session.add(
            _new_record(
                raw_challenge=raw_challenge,
                flow_id=flow_id,
                user_id=user_id,
                binding=binding,
                stage=1,
                expires_at=expires_at,
            )
        )
        await session.flush()
    return IssuedWriteChallenge(
        challenge=raw_challenge,
        stage=1,
        required_stages=binding.required_stages,
        expires_at=expires_at,
    )


async def advance_write_challenge(
    session: AsyncSession,
    *,
    user_id: str,
    challenge: str,
) -> IssuedWriteChallenge:
    now = utc_now()
    raw_next = token_urlsafe(32)
    async with session.begin():
        consumed = (
            update(WriteChallenge)
            .where(
                WriteChallenge.token_hash == _token_hash(challenge),
                WriteChallenge.user_id == user_id,
                WriteChallenge.consumed_at.is_(None),
                WriteChallenge.expires_at > now,
                WriteChallenge.stage < WriteChallenge.required_stages,
            )
            .values(consumed_at=now)
            .returning(
                WriteChallenge.flow_id,
                WriteChallenge.method,
                WriteChallenge.path,
                WriteChallenge.body_hash,
                WriteChallenge.stage,
                WriteChallenge.required_stages,
                WriteChallenge.expires_at,
            )
        )
        row = (await session.execute(consumed)).one_or_none()
        if row is None:
            raise _invalid_challenge()
        next_stage = int(row.stage) + 1
        binding = _WriteBinding(
            method=str(row.method),
            path=str(row.path),
            body_hash=str(row.body_hash),
            required_stages=int(row.required_stages),
        )
        session.add(
            _new_record(
                raw_challenge=raw_next,
                flow_id=str(row.flow_id),
                user_id=user_id,
                binding=binding,
                stage=next_stage,
                expires_at=row.expires_at,
            )
        )
        await session.flush()
    return IssuedWriteChallenge(
        challenge=raw_next,
        stage=next_stage,
        required_stages=binding.required_stages,
        expires_at=row.expires_at,
    )


async def consume_write_challenge(
    session: AsyncSession,
    *,
    user_id: str,
    challenge: str,
    method: str,
    path: str,
    body: Any,
    api_prefix: str,
) -> None:
    binding = _binding(method=method, path=path, body=body, api_prefix=api_prefix)
    now = utc_now()
    async with session.begin():
        consumed = (
            update(WriteChallenge)
            .where(
                WriteChallenge.token_hash == _token_hash(challenge),
                WriteChallenge.user_id == user_id,
                WriteChallenge.method == binding.method,
                WriteChallenge.path == binding.path,
                WriteChallenge.body_hash == binding.body_hash,
                WriteChallenge.required_stages == binding.required_stages,
                WriteChallenge.stage == WriteChallenge.required_stages,
                WriteChallenge.consumed_at.is_(None),
                WriteChallenge.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(
                WriteChallenge.flow_id,
                WriteChallenge.required_stages,
            )
        )
        row = (await session.execute(consumed)).one_or_none()
        if row is None:
            raise _invalid_challenge()
        if int(row.required_stages) > 1:
            prior_stage = await session.scalar(
                select(WriteChallenge.token_hash).where(
                    WriteChallenge.flow_id == row.flow_id,
                    WriteChallenge.user_id == user_id,
                    WriteChallenge.method == binding.method,
                    WriteChallenge.path == binding.path,
                    WriteChallenge.body_hash == binding.body_hash,
                    WriteChallenge.stage == int(row.required_stages) - 1,
                    WriteChallenge.consumed_at.is_not(None),
                )
            )
            if prior_stage is None:
                raise _invalid_challenge()


def _invalid_challenge() -> ConflictError:
    return ConflictError(
        "invalid_write_challenge",
        "The write challenge is invalid, expired, already used, or does not match this request",
    )
