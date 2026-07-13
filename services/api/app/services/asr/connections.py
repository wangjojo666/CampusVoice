import asyncio
import hashlib
from collections import defaultdict
from collections.abc import Awaitable
from secrets import token_urlsafe
from typing import Any, Protocol, cast

from redis.asyncio import Redis

from app.core.config import Settings


class AsrQuotaRegistry(Protocol):
    async def start(self) -> None: ...

    async def acquire(self, user_id: str, limit: int) -> str | None: ...

    async def release(self, user_id: str, lease_id: str) -> None: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


class AsrConnectionRegistry:
    """Process-local quota registry for an explicitly single-worker deployment."""

    def __init__(self) -> None:
        self._leases: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def acquire(self, user_id: str, limit: int) -> str | None:
        async with self._lock:
            if len(self._leases[user_id]) >= limit:
                return None
            lease_id = token_urlsafe(18)
            self._leases[user_id].add(lease_id)
            return lease_id

    async def release(self, user_id: str, lease_id: str) -> None:
        async with self._lock:
            leases = self._leases.get(user_id)
            if leases is None:
                return
            leases.discard(lease_id)
            if not leases:
                self._leases.pop(user_id, None)

    async def count(self, user_id: str) -> int:
        async with self._lock:
            return len(self._leases.get(user_id, ()))

    async def close(self) -> None:
        return None

    async def health_check(self) -> bool:
        return True


_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local clock = redis.call('TIME')
local now = (tonumber(clock[1]) * 1000) + math.floor(tonumber(clock[2]) / 1000)
local expires = now + tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local lease = ARGV[3]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, expires, lease)
redis.call('PEXPIREAT', key, expires)
return 1
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
redis.call('ZREM', key, ARGV[1])
if redis.call('ZCARD', key) == 0 then
  redis.call('DEL', key)
end
return 1
"""


class RedisAsrConnectionRegistry:
    """Cross-worker atomic per-user leases with crash-safe expiry."""

    def __init__(
        self,
        client: Redis,
        *,
        key_prefix: str,
        lease_ttl_ms: int,
        owns_client: bool = True,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix.rstrip(":")
        self._lease_ttl_ms = lease_ttl_ms
        self._owns_client = owns_client

    async def start(self) -> None:
        await self._client.ping()

    async def acquire(self, user_id: str, limit: int) -> str | None:
        lease_id = token_urlsafe(18)
        acquired = await cast(
            Awaitable[Any],
            self._client.eval(
                _ACQUIRE_SCRIPT,
                1,
                self._key(user_id),
                str(self._lease_ttl_ms),
                str(limit),
                lease_id,
            ),
        )
        return lease_id if int(acquired) == 1 else None

    async def release(self, user_id: str, lease_id: str) -> None:
        await cast(
            Awaitable[Any],
            self._client.eval(_RELEASE_SCRIPT, 1, self._key(user_id), lease_id),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        return bool(await self._client.ping())

    def _key(self, user_id: str) -> str:
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        return f"{self._key_prefix}:{user_hash}"


def build_asr_quota_registry(settings: Settings) -> AsrQuotaRegistry:
    if settings.asr_quota_backend == "local":
        return AsrConnectionRegistry()
    assert settings.asr_redis_url is not None
    client = Redis.from_url(
        settings.asr_redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    lease_ttl_ms = int(
        (settings.asr_max_session_seconds + settings.asr_quota_lease_grace_seconds) * 1000
    )
    return RedisAsrConnectionRegistry(
        client,
        key_prefix=settings.asr_redis_key_prefix,
        lease_ttl_ms=lease_ttl_ms,
    )
