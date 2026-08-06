import asyncio
import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

import httpx

from app.core.config import Settings

_WECHAT_CODE_EXCHANGE_URL = "https://api.weixin.qq.com/sns/jscode2session"
_MAX_RESPONSE_BYTES = 65_536


class WeChatError(Exception):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class WeChatIdentity:
    openid: str
    unionid: str | None = None


class WeChatClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        assert settings.wechat_app_id is not None
        assert settings.wechat_app_secret is not None
        self._app_id = settings.wechat_app_id
        self._app_secret = settings.wechat_app_secret.get_secret_value()
        self._timeout_seconds = settings.wechat_http_timeout_seconds
        self._transport = transport
        self._exchange_capacity = threading.BoundedSemaphore(
            settings.wechat_code_exchange_max_concurrency
        )
        self._rate_per_second = settings.wechat_code_exchange_rate_per_second
        self._burst = float(settings.wechat_code_exchange_burst)
        self._tokens = self._burst
        self._bucket_updated_at = clock()
        self._clock = clock
        self._bucket_lock = threading.Lock()

    def _consume_rate_token(self) -> None:
        with self._bucket_lock:
            now = self._clock()
            if now > self._bucket_updated_at:
                elapsed = now - self._bucket_updated_at
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate_per_second)
                self._bucket_updated_at = now
            if self._tokens < 1.0:
                retry_after = max(
                    1,
                    math.ceil((1.0 - self._tokens) / self._rate_per_second),
                )
                raise WeChatError(
                    "wechat_exchange_rate_exceeded",
                    retry_after_seconds=retry_after,
                )
            self._tokens -= 1.0

    async def exchange_code(self, code: str) -> WeChatIdentity:
        self._consume_rate_token()
        if not self._exchange_capacity.acquire(blocking=False):
            raise WeChatError("wechat_exchange_capacity_exceeded", retry_after_seconds=1)
        try:
            return await self._exchange_code(code)
        finally:
            self._exchange_capacity.release()

    async def _exchange_code(self, code: str) -> WeChatIdentity:
        params = {
            "appid": self._app_id,
            "secret": self._app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": "CampusVoice/0.3 WeChatAuth",
        }
        raw = bytearray()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(self._timeout_seconds),
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        _WECHAT_CODE_EXCHANGE_URL,
                        params=params,
                        headers=headers,
                    ) as response:
                        if response.is_redirect:
                            raise WeChatError("wechat_transport_rejected")
                        if response.status_code != 200:
                            raise WeChatError("wechat_service_unavailable")
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                                raise WeChatError("wechat_response_too_large")
                            raw.extend(chunk)
        except WeChatError:
            raise
        except (TimeoutError, httpx.HTTPError) as exc:
            raise WeChatError("wechat_service_unavailable") from exc
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeChatError("wechat_response_invalid") from exc
        if not isinstance(payload, dict):
            raise WeChatError("wechat_response_invalid")
        if payload.get("errcode") not in (None, 0):
            raise WeChatError("wechat_code_rejected")

        openid = payload.get("openid")
        unionid = payload.get("unionid")
        if not isinstance(openid, str) or not 1 <= len(openid) <= 128:
            raise WeChatError("wechat_identity_invalid")
        if unionid is not None and (not isinstance(unionid, str) or not 1 <= len(unionid) <= 128):
            raise WeChatError("wechat_identity_invalid")
        return WeChatIdentity(openid=openid, unionid=unionid)
