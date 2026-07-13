import asyncio
from collections import Counter


class AsrConnectionRegistry:
    """Process-local guard; deployments must use sticky single-worker ASR routing."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str, limit: int) -> bool:
        async with self._lock:
            if self._counts[user_id] >= limit:
                return False
            self._counts[user_id] += 1
            return True

    async def release(self, user_id: str) -> None:
        async with self._lock:
            if self._counts[user_id] <= 1:
                self._counts.pop(user_id, None)
            else:
                self._counts[user_id] -= 1

    async def count(self, user_id: str) -> int:
        async with self._lock:
            return self._counts[user_id]
