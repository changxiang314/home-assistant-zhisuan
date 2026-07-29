"""The ZhiSuan (挚算智联) integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.components import webhook as ha_webhook

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

    # 启动时恢复 token（如果重启后有存）
    # （这里靠 api 内部 _ensure_token_fresh 检查；reload 时由 reload 流程重新 login）

    # 注册 webhook（用 inspect 探测签名，兼容 HA 不同版本的 async_register）
    hook_id = entry.data.get("webhook_hook_id") or ha_webhook.async_generate_id()
    if "webhook_hook_id" not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "webhook_hook_id": hook_id},
        )
    view = ZhisuanWebhookView(hass, hook_id)

    webhook_registered = False
    try:
        import inspect
        sig = inspect.signature(ha_webhook.async_register)
        required = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(required) >= 5:
            # HA 2024.6+: async_register(hass, domain, name, webhook_id, handler)
            ha_webhook.async_register(hass, DOMAIN, "zhisuan", hook_id, view)
        elif len(required) == 3:
            # Older HA: async_register(hass, webhook_id, handler)
            ha_webhook.async_register(hass, hook_id, view)
        else:
            _LOGGER.warning(
                "Unknown webhook.async_register signature (required=%d); skipping",
                len(required),
            )
        webhook_registered = True
    except Exception:  # noqa: BLE001
        _LOGGER.exception(
            "Webhook registration failed; integration will run in polling-only mode"
        )

    # 订阅（webhook 没注册成功也不影响 setup，订阅失败就当没有实时推送）
    if webhook_registered:
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
        # 注销 webhook
        hook_id = entry.data.get("webhook_hook_id")
        if hook_id:
            try:
                ha_webhook.async_unregister(hass, hook_id)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Webhook unregister failed")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
