"""Webhook receiver and subscription manager for ZhiSuan (挚算智联).

Two responsibilities:

1. **Subscription management** — keep the cloud's webhook subscription pointed
   at our current public URL. Because the cloudflared "quick tunnel" URL can
   change on restart, we re-subscribe whenever the public URL drifts.

2. **Inbound webhook** — receive the cloud's POST notifications and dispatch
   them to the entry's coordinator / data store.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp.client_exceptions import ClientConnectorError

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import get_url

from .api import ZhisuanApi, ZhisuanApiError
from .const import (
    CLOUDFLARED_METRICS_PORT,
    DEVICE_TYPE_PLUG,
    DOMAIN,
    MSG_TYPE_DEVICE_OFFLINE,
    MSG_TYPE_DEVICE_ONLINE,
    MSG_TYPE_DEVICE_REPORT,
    WEBHOOK_RESUBSCRIBE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

WEBHOOK_VIEW_NAME = "api:zhisuan:webhook"
WEBHOOK_PATH_TEMPLATE = "/api/zhisuan/webhook/{hook_id}"

# 挚算推送的 Notify Object → 我们要用的字段
DEVICE_REPORT_KEYS = {
    "turnOnOff",
    "brightness",
    "whiteBrightness",
    "colorTemperature",
    "color",
    "position",
    "temperature",
    "mode",
    "operationMode",
    "windSpeed",
    "currentTemperature",
    "humidity",
    "battery",
    "illuminance",
    "PM25",
    "co2",
    "motionAlarmState",
    "contactState",
    "waterSensorState",
    "smokeSensorState",
    "gasSensorState",
    "sosState",
}


# ----------------------------------------------------------------------
# 公网 URL 解析
# ----------------------------------------------------------------------
async def async_resolve_public_url(hass: HomeAssistant) -> str | None:
    """Resolve the public URL HA is reachable from the internet.

    Priority (优先国内可访问的 ddnsto / external_url，再退到 Nabu Casa / Cloudflared)：
    0. external_url（ddnsto / 自己域名）
    1. Cloudflared Add-on's quick-tunnel URL
    2. HA Nabu Casa cloud URL
    """
    # 0. external_url（国内 ddnsto 最稳，优先）
    try:
        url = get_url(hass, prefer_external=True)
        if url and "localhost" not in url and "127.0.0.1" not in url:
            _LOGGER.warning("Using external_url: %s", url)
            return url.rstrip("/")
    except Exception:  # noqa: BLE001
        pass

    # 1. Cloudflared 短隧道
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://localhost:{CLOUDFLARED_METRICS_PORT}/quicktunnel",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    host = data.get("hostname") or data.get("url")
                    if host:
                        # data 可能是 "https://xxx.trycloudflare.com" 或纯 hostname
                        url = host if host.startswith("http") else f"https://{host}"
                        _LOGGER.warning("Detected cloudflared URL: %s", url)
                        return url.rstrip("/")
    except (ClientConnectorError, asyncio.TimeoutError, aiohttp.ClientError):
        pass  # Cloudflared 没装或没起 — 继续往下试

    # 2. Nabu Casa（GFW 屏蔽，搁最后）
    try:
        from homeassistant.components.cloud import (  # type: ignore[attr-defined]
            CloudNotAvailable,
            async_remote_ui_url,
        )

        cloud_url = async_remote_ui_url(hass)
        if cloud_url:
            _LOGGER.warning("Using Nabu Casa URL: %s", cloud_url)
            return cloud_url.rstrip("/")
    except (ImportError, CloudNotAvailable, Exception):  # noqa: BLE001
        pass

    return None


def build_webhook_url(public_base: str, hook_id: str) -> str:
    """Build the full webhook URL the cloud should POST to."""
    return f"{public_base.rstrip('/')}/api/zhisuan/webhook/{hook_id}"


# ----------------------------------------------------------------------
# 订阅管理
# ----------------------------------------------------------------------
async def async_subscribe(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: ZhisuanApi,
    hook_id: str,
) -> bool:
    """(Re)subscribe the cloud to push to our current webhook URL."""
    public_base = await async_resolve_public_url(hass)
    if not public_base:
        _LOGGER.warning(
            "Cannot determine public URL; webhook not subscribed. "
            "Check Cloudflared Add-on or HA external_url."
        )
        return False

    notify_uri = build_webhook_url(public_base, hook_id)
    home_id = entry.data.get("home_id", 0)
    try:
        await api.async_subscribe(home_id, notify_uri)
    except ZhisuanApiError as err:
        _LOGGER.error("Subscribe failed: %s", err)
        return False
    _LOGGER.info("Subscribed home %s to %s", home_id, notify_uri)
    return True


async def async_unsubscribe(api: ZhisuanApi, home_id: int) -> None:
    try:
        await api.async_unsubscribe(home_id)
    except ZhisuanApiError as err:
        _LOGGER.warning("Unsubscribe failed: %s", err)


# ----------------------------------------------------------------------
# 入站 Webhook 端点
# ----------------------------------------------------------------------
class ZhisuanWebhookView(HomeAssistantView):
    """Receive ZhiSuan push notifications.

    POST /api/zhisuan/webhook/<hook_id>
    Body: Notify Object
    """

    url = WEBHOOK_PATH_TEMPLATE
    name = WEBHOOK_VIEW_NAME
    requires_auth = False  # 挚算云推过来，没有 HA token
    csrf = False

    def __init__(self, hass: HomeAssistant, hook_id: str) -> None:
        self.hass = hass
        self.hook_id = hook_id

    async def post(self, request: web.Request, hook_id: str) -> web.Response:
        if hook_id != self.hook_id:
            return web.Response(status=404, text="unknown hook id")

        try:
            payload: dict[str, Any] = await request.json()
        except (ValueError, TypeError) as err:
            _LOGGER.warning("Webhook received invalid JSON: %s", err)
            return web.Response(status=400, text="bad json")

        _LOGGER.debug("Webhook payload: %s", payload)
        # 直接在事件循环里 fire-and-forget 调度任务，HA 2026 禁止跨线程 async_create_task
        self.hass.async_create_task(self._dispatch(payload))
        # 立即 200 OK — 挚算期望快速 ack
        return web.Response(status=200, text="ok")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        msg_type = payload.get("messageType")
        data = payload.get("data") or {}

        # 找到所有这个 hook_id 对应的 entry
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data.get("webhook_hook_id") != self.hook_id:
                continue
            store = self.hass.data[DOMAIN].get(entry.entry_id)
            if not store:
                continue
            coordinator = store.get("coordinator")
            if not coordinator:
                continue

            if msg_type == MSG_TYPE_DEVICE_REPORT:
                user_device_id = data.get("userDeviceId")
                ext = data.get("extension") or {}
                # 提取已知属性
                props = {
                    k: v for k, v in ext.items() if k in DEVICE_REPORT_KEYS
                }
                if user_device_id is not None and props:
                    coordinator.async_apply_push(user_device_id, props)
                    # Plug 设备的 on/off 变化时，立刻 query 一次实时功率，
                    # 这样 power sensor 在 1-2s 内就反映新状态（不用等 60s 轮询）
                    if "turnOnOff" in props:
                        device = coordinator.get_device(user_device_id)
                        if device and device.get("type") == DEVICE_TYPE_PLUG:
                            self.hass.async_create_task(
                                coordinator.async_refresh_plug_power(user_device_id)
                            )

            elif msg_type in (MSG_TYPE_DEVICE_ONLINE, MSG_TYPE_DEVICE_OFFLINE):
                user_device_id = data.get("userDeviceId")
                if user_device_id is not None:
                    coordinator.async_apply_push(
                        user_device_id,
                        {"isOnline": msg_type == MSG_TYPE_DEVICE_ONLINE},
                    )

            else:
                _LOGGER.debug("Unhandled messageType=%s payload=%s", msg_type, payload)


# ----------------------------------------------------------------------
# 周期重订阅（兜底：cloudflared URL 漂移）
# ----------------------------------------------------------------------
async def async_resubscribe_loop(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: ZhisuanApi,
    hook_id: str,
    stop_event: asyncio.Event,
) -> None:
    """Re-check public URL every WEBHOOK_RESUBSCRIBE_INTERVAL; re-subscribe on drift."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=WEBHOOK_RESUBSCRIBE_INTERVAL
            )
        except asyncio.TimeoutError:
            pass
        else:
            return  # stopped
        try:
            await async_subscribe(hass, entry, api, hook_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Resubscribe failed; will retry next cycle")
