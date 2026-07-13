import os
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis

from app.core.config import Settings
from app.services.asr.connections import AsrConnectionRegistry, RedisAsrConnectionRegistry


@pytest.mark.asyncio
async def test_local_asr_quota_uses_idempotent_leases() -> None:
    registry = AsrConnectionRegistry()
    first = await registry.acquire("student", 1)
    assert first is not None
    assert await registry.acquire("student", 1) is None
    await registry.release("student", first)
    await registry.release("student", first)
    assert await registry.count("student") == 0


class _RedisStub:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def ping(self) -> bool:
        return True

    async def eval(self, *args: object) -> int:
        self.calls.append(args)
        return 1

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_redis_asr_quota_uses_server_time_hashed_user_and_atomic_scripts() -> None:
    client = _RedisStub()
    registry = RedisAsrConnectionRegistry(  # type: ignore[arg-type]
        client, key_prefix="cv:quota", lease_ttl_ms=30_000, owns_client=False
    )
    await registry.start()
    lease = await registry.acquire("student-private-id", 2)
    assert lease is not None
    acquire_call = client.calls[0]
    assert "student-private-id" not in str(acquire_call)
    assert "redis.call('TIME')" in str(acquire_call[0])
    assert acquire_call[3] == "30000"
    assert acquire_call[4] == "2"
    await registry.release("student-private-id", lease)
    assert len(client.calls) == 2


def test_multiple_workers_fail_closed_without_redis() -> None:
    with pytest.raises(ValidationError, match="multiple ASR workers require"):
        Settings(env="test", asr_worker_count=2)
    with pytest.raises(ValidationError, match="multiple workers require a shared"):
        Settings(
            env="test",
            asr_worker_count=2,
            asr_quota_backend="redis",
            asr_redis_url=SecretStr("redis://localhost:6379/0"),
        )
    configured = Settings(
        env="test",
        asr_worker_count=2,
        asr_quota_backend="redis",
        asr_redis_url=SecretStr("redis://localhost:6379/0"),
        confirmation_secret=SecretStr("shared-worker-confirmation-secret-at-least-32-bytes"),
    )
    assert configured.asr_quota_backend == "redis"


@pytest.mark.asyncio
async def test_redis_quota_is_shared_across_registry_instances() -> None:
    url = os.getenv("CAMPUSVOICE_TEST_REDIS_URL")
    if not url:
        pytest.skip("CAMPUSVOICE_TEST_REDIS_URL is not configured")
    prefix = f"campusvoice:test:{uuid4().hex}"
    first_client = Redis.from_url(url, decode_responses=True)
    second_client = Redis.from_url(url, decode_responses=True)
    first = RedisAsrConnectionRegistry(first_client, key_prefix=prefix, lease_ttl_ms=10_000)
    second = RedisAsrConnectionRegistry(second_client, key_prefix=prefix, lease_ttl_ms=10_000)
    try:
        await first.start()
        await second.start()
        lease = await first.acquire("shared-user", 1)
        assert lease is not None
        assert await second.acquire("shared-user", 1) is None
        await first.release("shared-user", lease)
        second_lease = await second.acquire("shared-user", 1)
        assert second_lease is not None
        await second.release("shared-user", second_lease)
    finally:
        await first.close()
        await second.close()
