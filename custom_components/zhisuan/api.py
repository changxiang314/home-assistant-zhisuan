"""Async API client for ZhiSuan (挚算智联) cloud platform."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientTimeout

from .const import (
    API_BASE_URLS,
    API_VERSION,
    CONF_COUNTRY_CODE,
    DEFAULT_COUNTRY_CODE,
    DEFAULT_ENVIRONMENT,
    DEFAULT_LANGUAGE,
    DEFAULT_TIMEOUT,
    DEVICE_BY_ID_URL,
    DEVICE_CONTROL_URL,
    DEVICE_LIST_URL,
    HOME_LIST_URL,
    OAUTH_AUTHORIZE_URL,
    OAUTH_TOKEN_URL,
    REGISTER_URL,
    ROOM_LIST_URL,
    SUBSCRIBE_URL,
    TOKEN_REFRESH_MARGIN,
    UNSUBSCRIBE_URL,
)

_LOGGER = logging.getLogger(__name__)


class ZhisuanApiError(Exception):
    """Base exception for ZhiSuan API errors."""


class ZhisuanAuthError(ZhisuanApiError):
    """Authentication failed (invalid/expired token)."""


class ZhisuanConnectionError(ZhisuanApiError):
    """Network / connection error."""


class ZhisuanApi:
    """Async client for the ZhiSuan Open API.

    Responsibilities:
    - Hold credentials and current OAuth tokens.
    - Provide typed methods for every supported REST endpoint.
    - Auto-refresh access_token before expiry.
    - Surface API-level errors as exceptions.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        session: aiohttp.ClientSession,
        *,
        environment: str = DEFAULT_ENVIRONMENT,
        region: str = "LOCAL",
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session
        self._environment = environment
        self._region = region
        self._language = language
        self._country_code = DEFAULT_COUNTRY_CODE
        self._base_url = API_BASE_URLS[environment]

        # OAuth token state
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0  # epoch seconds
        self._username: str | None = None
        self._password: str | None = None

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_token(self) -> bool:
        return self._access_token is not None

    @property
    def token_expires_at(self) -> float:
        return self._token_expires_at

    @property
    def refresh_token(self) -> str | None:
        return self._refresh_token

    @property
    def access_token(self) -> str | None:
        return self._access_token

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------
    async def async_login(self, username: str, password: str) -> None:
        """Log in with username/password, perform full OAuth dance, store tokens.

        Steps: get authorization code → exchange for tokens.
        """
        self._username = username
        self._password = password

        code_resp = await self._async_post_form(
            OAUTH_AUTHORIZE_URL,
            data={
                "username": username,
                "userPassword": password,
                "client_id": self._client_id,
                "regionId": self._region or "",
                "response_type": "code",
                "countryCode": self._country_code,
            },
        )
        code = code_resp["data"]["code"]
        await self._async_exchange_code(code)

    async def async_refresh_tokens(self) -> None:
        """Refresh access_token using stored refresh_token."""
        if not self._refresh_token:
            raise ZhisuanAuthError("No refresh_token available; need to re-login.")
        resp = await self._async_post_form(
            OAUTH_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
                "redirect_uri": "http://",
            },
        )
        self._store_token_response(resp)

    async def async_register_user(self, username: str, password: str) -> None:
        """Register a new user account (rarely needed; mainly for first-time setup)."""
        await self._async_post_json(
            REGISTER_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "regionId": self._region,
                "username": username,
                "userPassword": password,
            },
        )

    # ------------------------------------------------------------------
    # 家庭 / 房间
    # ------------------------------------------------------------------
    async def async_get_homes(self) -> list[dict[str, Any]]:
        """Get list of homes."""
        resp = await self._async_get(HOME_LIST_URL)
        return resp["data"]["list"]

    async def async_get_rooms(self, home_id: int) -> list[dict[str, Any]]:
        """Get rooms under a home. Note: this endpoint may return rooms inside
        a home object; we try both shapes."""
        resp = await self._async_get(ROOM_LIST_URL, params={"homeId": home_id})
        data = resp.get("data") or {}
        # Some endpoints return Page<Room>, some return List<Room> directly
        if isinstance(data, dict) and "list" in data:
            return data["list"]
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    async def async_get_devices(
        self,
        home_id: int,
        *,
        page: int = 1,
        page_size: int = 50,
        **filters: Any,
    ) -> dict[str, Any]:
        """Get a page of devices under a home. Returns the page object:
        {page, pageSize, pageTotal, dataTotal, list: [Device, ...]}
        """
        params: dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
            "homeId": home_id,
        }
        params.update(filters)
        resp = await self._async_get(DEVICE_LIST_URL, params=params)
        return resp["data"]

    async def async_get_all_devices(self, home_id: int) -> list[dict[str, Any]]:
        """Page through and return ALL devices under a home."""
        all_devices: list[dict[str, Any]] = []
        page = 1
        while True:
            page_obj = await self.async_get_devices(home_id, page=page, page_size=50)
            all_devices.extend(page_obj.get("list") or [])
            page_total = page_obj.get("pageTotal", 1)
            if page >= page_total:
                break
            page += 1
        return all_devices

    async def async_get_device(self, user_device_id: int, home_id: int) -> dict[str, Any]:
        """Get a single device by ID."""
        url = DEVICE_BY_ID_URL.format(user_device_id=user_device_id)
        resp = await self._async_get(url, params={"homeId": home_id})
        return resp["data"]["device"]

    async def async_control_device(
        self,
        user_device_id: int,
        action: str,
        *,
        home_id: int,
        extension: dict[str, Any] | None = None,
    ) -> None:
        """Send a control command to a device. The API will ack the request
        and asynchronously push a DeviceAction notification on success."""
        payload: dict[str, Any] = {
            "userDeviceId": user_device_id,
            "name": action,
        }
        if extension:
            payload["extension"] = extension
        await self._async_post_json(
            DEVICE_CONTROL_URL,
            data=payload,
            home_id=home_id,
        )

    # ------------------------------------------------------------------
    # Webhook 订阅
    # ------------------------------------------------------------------
    async def async_subscribe(self, home_id: int, notify_uri: str) -> None:
        """Subscribe to home-level notifications. The cloud will POST
        device status changes to notify_uri."""
        await self._async_post_json(
            SUBSCRIBE_URL,
            data={"homeId": str(home_id), "notifyUri": notify_uri},
            home_id=home_id,
        )

    async def async_unsubscribe(self, home_id: int) -> None:
        """Cancel home-level notification subscription."""
        url = UNSUBSCRIBE_URL.format(home_id=home_id)
        await self._async_delete(url, home_id=home_id)

    # ------------------------------------------------------------------
    # 内部：HTTP 包装
    # ------------------------------------------------------------------
    async def _async_exchange_code(self, code: str) -> None:
        resp = await self._async_post_form(
            OAUTH_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": "http://",
            },
        )
        self._store_token_response(resp)

    def _store_token_response(self, resp: dict[str, Any]) -> None:
        data = resp.get("data") or resp
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        expires_in = int(data.get("expires_in", 7776000))
        # 提前 TOKEN_REFRESH_MARGIN 秒刷新，留缓冲
        self._token_expires_at = time.time() + max(expires_in - TOKEN_REFRESH_MARGIN, 60)

    async def _ensure_token_fresh(self) -> None:
        if not self._access_token:
            raise ZhisuanAuthError("Not logged in.")
        if time.time() < self._token_expires_at:
            return
        _LOGGER.debug("Access token near expiry, refreshing")
        await self.async_refresh_tokens()

    def _auth_headers(self, home_id: int | None) -> dict[str, str]:
        if not self._access_token:
            raise ZhisuanAuthError("Not logged in.")
        headers = {
            "clientId": self._client_id,
            "authorization": self._access_token,
            "language": self._language,
            "version": API_VERSION,
        }
        if home_id is not None:
            headers["homeId"] = str(home_id)
        return headers

    async def _async_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        home_id: int | None = None,
    ) -> dict[str, Any]:
        await self._ensure_token_fresh()
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                headers=self._auth_headers(home_id),
                timeout=ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                return await self._parse_response(resp)
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise ZhisuanAuthError(str(err)) from err
            raise ZhisuanApiError(f"GET {path} failed: {err}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise ZhisuanConnectionError(f"GET {path} failed: {err}") from err

    async def _async_post_json(
        self,
        path: str,
        *,
        data: dict[str, Any],
        home_id: int | None = None,
    ) -> dict[str, Any]:
        await self._ensure_token_fresh()
        url = f"{self._base_url}{path}"
        headers = self._auth_headers(home_id)
        headers["content-type"] = "application/json"
        try:
            async with self._session.post(
                url,
                json=data,
                headers=headers,
                timeout=ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                return await self._parse_response(resp)
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise ZhisuanAuthError(str(err)) from err
            raise ZhisuanApiError(f"POST {path} failed: {err}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise ZhisuanConnectionError(f"POST {path} failed: {err}") from err

    async def _async_post_form(
        self,
        path: str,
        *,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """POST with application/x-www-form-urlencoded (used by OAuth)."""
        url = f"{self._base_url}{path}"
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "language": self._language,
            "version": API_VERSION,
        }
        encoded = urlencode(data, doseq=True)
        try:
            async with self._session.post(
                url,
                data=encoded,
                headers=headers,
                timeout=ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                return await self._parse_response(resp)
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise ZhisuanAuthError(str(err)) from err
            raise ZhisuanApiError(f"POST {path} failed: {err}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise ZhisuanConnectionError(f"POST {path} failed: {err}") from err

    async def _async_delete(
        self,
        path: str,
        *,
        home_id: int | None = None,
    ) -> dict[str, Any]:
        await self._ensure_token_fresh()
        url = f"{self._base_url}{path}"
        try:
            async with self._session.delete(
                url,
                headers=self._auth_headers(home_id),
                timeout=ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                return await self._parse_response(resp)
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise ZhisuanAuthError(str(err)) from err
            raise ZhisuanApiError(f"DELETE {path} failed: {err}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise ZhisuanConnectionError(f"DELETE {path} failed: {err}") from err

    @staticmethod
    async def _parse_response(resp: aiohttp.ClientResponse) -> dict[str, Any]:
        """Parse response and raise on business-level errors."""
        text = await resp.text()
        try:
            payload: dict[str, Any] = await resp.json(content_type=None)
        except Exception as err:
            raise ZhisuanApiError(
                f"Invalid JSON response (status {resp.status}): {text[:200]}"
            ) from err

        # OAuth endpoints return either {"access_token": ...} (no "code" field)
        # or {"error": ..., "error_description": ...}
        if "error" in payload:
            raise ZhisuanAuthError(
                f"OAuth error: {payload.get('error')}: {payload.get('error_description')}"
            )
        if "access_token" in payload:
            return payload

        # Business endpoints return {"code": 200, "info": "SUCCESS", "data": ...}
        code = payload.get("code")
        if code is not None and code != 200:
            raise ZhisuanApiError(
                f"API error code={code} info={payload.get('info')} data={payload.get('data')}"
            )

        # HTTP 200 但 body 完全空 / 不是预期格式 → 视为鉴权失败
        # （挚算在 clientId 失效时偶尔会回 200 + 空 body，claude 调试发现）
        if resp.status in (401, 403) or not payload:
            raise ZhisuanAuthError(
                f"Auth failure (status {resp.status}): {text[:200]}"
            )
        return payload
