"""Data update coordinator for ZhiSuan (挚算智联).

Maintains the in-memory device cache and feeds it to entity platforms.

Two data sources feed the cache:
- Periodic full refresh (fallback, 60s by default)
- Webhook push notifications (real-time)

The push path is the primary mechanism; the periodic refresh is just a safety
net in case a push is missed (e.g. cloudflared URL changed mid-flight).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZhisuanApi, ZhisuanApiError, ZhisuanAuthError
from .const import BROKEN_PARENT_IDS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Webhook 兜底：即使实时推送也每 60s 拉一次全量，防止丢消息
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)


class ZhisuanCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Coordinator for ZhiSuan devices.

    `data` is keyed by userDeviceId (int) and contains the full Device object
    as returned by the API.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ZhisuanApi,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.entry_id})",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self.api = api
        self._home_id: int = int(entry.data.get("home_id", 0))
        self._devices: dict[int, dict[str, Any]] = {}
        self._rooms: dict[int, str] = {}
        # 推送缓冲：等下一次 update 时合并
        self._pending_pushes: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------
    @property
    def home_id(self) -> int:
        return self._home_id

    @property
    def devices(self) -> dict[int, dict[str, Any]]:
        return self._devices

    def get_device(self, user_device_id: int) -> dict[str, Any] | None:
        return self._devices.get(user_device_id)

    def get_room_name(self, room_id: int) -> str | None:
        return self._rooms.get(room_id)

    # ------------------------------------------------------------------
    # 主循环：拉全量
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        try:
            # 拉设备列表
            all_devices = await self.api.async_get_all_devices(self._home_id)
            # 拉房间列表（best-effort）
            try:
                rooms = await self.api.async_get_rooms(self._home_id)
                self._rooms = {r["roomId"]: r.get("roomName", "") for r in rooms}
            except ZhisuanApiError as err:
                _LOGGER.debug("Fetch rooms failed (non-fatal): %s", err)

            # 应用 webhook 缓冲里的未合并推送
            new_cache: dict[int, dict[str, Any]] = {
                d["userDeviceId"]: d for d in all_devices
            }
            for user_device_id, props in self._pending_pushes.items():
                if user_device_id in new_cache:
                    cache = new_cache[user_device_id].setdefault("cache", {})
                    ext = cache.setdefault("extension", {})
                    ext.update(props)
            self._pending_pushes.clear()
            # 排除已知挚算云状态不同步的设备（详见 const.BROKEN_PARENT_IDS）
            skipped = {
                uid for uid, dev in new_cache.items()
                if dev.get("parentId") in BROKEN_PARENT_IDS
                or uid in BROKEN_PARENT_IDS
            }
            for uid in skipped:
                new_cache.pop(uid, None)
            self._devices = new_cache
            _LOGGER.info(
                "ZhiSuan refresh OK: home_id=%s, devices=%d (skipped %d broken), rooms=%d",
                self._home_id, len(self._devices), len(skipped), len(self._rooms),
            )
            return self._devices

        except ZhisuanAuthError as err:
            raise UpdateFailed(f"Auth error: {err}") from err
        except ZhisuanApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    # ------------------------------------------------------------------
    # Webhook 推送入口（线程安全：从 webhook view 调）
    # ------------------------------------------------------------------
    @callback
    def async_apply_push(
        self, user_device_id: int, props: dict[str, Any]
    ) -> None:
        """Apply a single device's pushed state change immediately.

        Updates the in-memory cache directly (so listeners get notified via
        _devices mutation tracking), and queues the change so the next
        periodic refresh re-fetches the authoritative state.
        """
        if user_device_id not in self._devices:
            _LOGGER.debug(
                "Push for unknown device %s; will pick up on next refresh",
                user_device_id,
            )
            # 仍然缓冲，下次 _async_update_data 时如果拉到了这个设备就合并
            self._pending_pushes[user_device_id] = (
                self._pending_pushes.get(user_device_id, {})
            )
            self._pending_pushes[user_device_id].update(props)
            return

        device = self._devices[user_device_id]
        cache = device.setdefault("cache", {})
        ext = cache.setdefault("extension", {})
        ext.update(props)
        # 触发 listener
        self.async_set_updated_data(self._devices)

    # ------------------------------------------------------------------
    # 设备控制：透传给 api
    # ------------------------------------------------------------------
    async def async_control(
        self,
        user_device_id: int,
        action: str,
        extension: dict[str, Any] | None = None,
    ) -> None:
        """Send a control command. Webhook push will deliver the new state."""
        await self.api.async_control_device(
            user_device_id=user_device_id,
            action=action,
            home_id=self._home_id,
            extension=extension,
        )
