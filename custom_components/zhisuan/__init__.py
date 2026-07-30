"""The ZhiSuan (挚算智联) integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZhisuanApi, ZhisuanApiError, ZhisuanAuthError, ZhisuanConnectionError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_ENVIRONMENT,
    CONF_HOME_ID,
    CONF_REGION,
    DEFAULT_ENVIRONMENT,
    DOMAIN,
)
from .coordinator import ZhisuanCoordinator
from .webhook import (
    ZhisuanWebhookView,
    async_resubscribe_loop,
    async_subscribe as async_subscribe_webhook,
    async_unsubscribe as async_unsubscribe_webhook,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZhiSuan from a config entry."""
    session = async_get_clientsession(hass)
    api = ZhisuanApi(
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        session=session,
        environment=entry.data.get(CONF_ENVIRONMENT, DEFAULT_ENVIRONMENT),
        region=entry.data.get(CONF_REGION, "CN"),
    )

    # OAuth login（保存 refresh_token 到 entry 暂存）
    try:
        await api.async_login(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
    except ZhisuanAuthError as err:
        _LOGGER.error("OAuth failed during setup: %s", err)
        return False
    except (ZhisuanApiError, ZhisuanConnectionError) as err:
        raise ConfigEntryNotReady(f"Cannot reach ZhiSuan cloud: {err}") from err

    # 持久化 token 到 entry（这样重启后能复用，不用每次重新走 OAuth）
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "access_token": api.access_token,
            "refresh_token": api.refresh_token,
            "token_expires_at": api.token_expires_at,
        },
    )

    # Coordinator
    coordinator = ZhisuanCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    # 一次性迁移：v1.2.4 之前给 Light/Plug/Switch/Button 错误创建了 battery sensor，
    # 导致这些 device 在 HA 里被标 unavailable。启动时清掉。
    _migrate_stale_battery_sensors(hass, coordinator)

    # 启动时恢复 token（如果重启后有存）
    # （这里靠 api 内部 _ensure_token_fresh 检查；reload 时由 reload 流程重新 login）

    # 注册 webhook（用 hass.http.register_view 老 API，跨 HA 版本兼容）
    hook_id = entry.data.get("webhook_hook_id")
    if not hook_id:
        import secrets
        hook_id = secrets.token_hex(16)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "webhook_hook_id": hook_id},
        )
    view = ZhisuanWebhookView(hass, hook_id)
    try:
        hass.http.register_view(view)
        _LOGGER.info(
            "Webhook registered: POST %s/api/zhisuan/webhook/<id>",
            "<public-url-of-this-HA>",
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Webhook register failed; integration will run in polling-only mode"
        )

    # 订阅（失败也不影响 setup，周期任务会重试）
    try:
        await async_subscribe_webhook(hass, entry, api, hook_id)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Initial subscribe failed; will retry in background loop")

    # 周期重订阅
    stop_event = asyncio.Event()
    resubscribe_task = hass.async_create_task(
        async_resubscribe_loop(hass, entry, api, hook_id, stop_event)
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "webhook_view": view,
        "stop_event": stop_event,
        "resubscribe_task": resubscribe_task,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if store:
        # 停止重订阅循环
        store["stop_event"].set()
        store["resubscribe_task"].cancel()
        # 取消订阅挚算云
        try:
            home_id = int(entry.data.get(CONF_HOME_ID, 0))
            await async_unsubscribe_webhook(store["api"], home_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unsubscribe on unload failed")
        # 注销 webhook（hass.http.register_view 注册的 route 由 HA 自行清理）
        # 这里不显式反注册，避免 view 实例的引用问题

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _migrate_stale_battery_sensors(
    hass: HomeAssistant, coordinator: "ZhisuanCoordinator"
) -> None:
    """删除 v1.2.4 之前为 Light/Plug/Switch/Button 错误创建的 battery sensor。

    这些 sensor 的 unique_id 格式是 "{user_device_id}_battery"。
    如果对应设备 type 是 Light/Plug/Switch/Button，就删掉。
    """
    # 哪些 type 的 device 不应该有 battery sensor
    blocked_types = {"Light", "Plug", "Switch", "Button"}
    registry = er.async_get(hass)
    removed = 0
    for entity in list(registry.entities.values()):
        if entity.platform != DOMAIN:
            continue
        if not entity.unique_id.endswith("_battery"):
            continue
        uid_str = entity.unique_id[: -len("_battery")]
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        device = coordinator.devices.get(uid)
        if not device:
            continue
        if device.get("type") in blocked_types:
            _LOGGER.info(
                "Migration: removing stale battery sensor %s (device type=%s, uid=%s)",
                entity.entity_id, device.get("type"), uid,
            )
            registry.async_remove(entity.entity_id)
            removed += 1
    if removed:
        _LOGGER.info("Migration: removed %d stale battery sensor(s)", removed)
