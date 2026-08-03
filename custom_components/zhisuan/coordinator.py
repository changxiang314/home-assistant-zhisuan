"""Data update coordinator for ZhiSuan (挚算智联).

Maintains the in-memory device cache and feeds it to entity platforms.

Two data sources feed the cache:
- Periodic full refresh (fallback, 60s by default)
- Webhook push notifications (real-time)

The push path is the primary mechanism; the periodic refresh is just a safety
net in case a push is missed (e.g. cloudflared URL changed mid-flight).

A separate, high-frequency background task (PLUG_POWER_POLL_INTERVAL = 5s by
default) polls the real-time power for Plug devices via QueryDisconnector,
because the cloud does NOT push power changes (no messageType for it).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZhisuanApi, ZhisuanApiError, ZhisuanAuthError
from .const import (
    ACTION_QUERY_DISCONNECTOR,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_PLUG,
    DOMAIN,
    PLUG_POWER_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


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
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.api = api
        self._home_id: int = int(entry.data.get("home_id", 0))
        self._devices: dict[int, dict[str, Any]] = {}
        self._rooms: dict[int, str] = {}
        # 推送缓冲：等下一次 update 时合并
        self._pending_pushes: dict[int, dict[str, Any]] = {}
        # Plug 设备实时功率（userDeviceId → W，None 表示还没拉到）
        self._plug_power: dict[int, float | None] = {}
        # Plug 功率后台轮询：独立于主 60s 循环
        self._plug_poll_task: asyncio.Task | None = None
        self._plug_poll_stop: asyncio.Event | None = None
        self._plug_poll_interval: float = PLUG_POWER_POLL_INTERVAL

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

    def get_plug_power(self, user_device_id: int) -> float | None:
        """Return the last-known real-time power (W) for a Plug device.

        ``None`` means we have never successfully pulled it.
        """
        return self._plug_power.get(user_device_id)

    # ------------------------------------------------------------------
    # Plug 功率后台轮询：独立于主循环
    # ------------------------------------------------------------------
    @callback
    def async_start_plug_power_poll(self) -> None:
        """Start (or no-op if already started) the high-frequency plug power poll.

        Idempotent — safe to call multiple times (e.g. on reload).
        """
        if self._plug_poll_task is not None and not self._plug_poll_task.done():
            return
        self._plug_poll_stop = asyncio.Event()
        self._plug_poll_task = self.hass.async_create_task(
            self._plug_power_poll_loop()
        )
        _LOGGER.info(
            "Plug power poll started: every %.1fs (cloud does not push power)",
            self._plug_poll_interval,
        )

    async def async_stop_plug_power_poll(self) -> None:
        """Stop the plug power poll task and wait for it to finish."""
        if self._plug_poll_stop is not None:
            self._plug_poll_stop.set()
        task = self._plug_poll_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._plug_poll_task = None
        self._plug_poll_stop = None

    async def _plug_power_poll_loop(self) -> None:
        """Background loop: poll plug power at PLUG_POWER_POLL_INTERVAL.

        Cloud does not push power changes, so this is the only way to get
        real-time updates. Each iteration failure is logged at DEBUG and
        the loop continues — a single bad query must not stop polling.
        """
        stop = self._plug_poll_stop
        interval = self._plug_poll_interval
        if stop is None:
            return

        # 等到至少一个 Plug 设备出现（避免在没设备的家庭里空转）
        while not stop.is_set():
            if any(
                d.get("type") == DEVICE_TYPE_PLUG
                for d in self._devices.values()
            ):
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                continue
            else:
                return

        # 第一次立即拉一次（不等 5s），让 sensor 立刻有值
        try:
            await self.async_refresh_plug_power()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Plug power poll (initial) failed", exc_info=True)

        # 周期循环
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            else:
                return  # stop event set → 退出
            try:
                await self.async_refresh_plug_power()
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Plug power poll iteration failed (continuing)", exc_info=True
                )

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------
    @property
    def home_id(self) -> int:
        return self._home_id

    @property
    def devices(self) -> dict[int, dict[str, Any]]:
        return self._devices

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
            self._devices = new_cache
            _LOGGER.info(
                "ZhiSuan refresh OK: home_id=%s, devices=%d, rooms=%d",
                self._home_id, len(self._devices), len(self._rooms),
            )

            # 拉每个 Plug 设备的实时功率（不阻塞主流程）
            await self.async_refresh_plug_power()

            return self._devices

        except ZhisuanAuthError as err:
            raise UpdateFailed(f"Auth error: {err}") from err
        except ZhisuanApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    # ------------------------------------------------------------------
    # Plug 实时功率：每个 update 周期拉一次 + 事件触发即时刷
    # ------------------------------------------------------------------
    async def async_refresh_plug_power(self, user_device_id: int | None = None) -> None:
        """Query Plug device(s) for real-time power via QueryDisconnector.

        - Called every coordinator cycle (60s) to keep the value fresh
        - Also called by the webhook handler when a Plug's on/off state changes,
          so the power sensor reflects the new state within ~1-2s instead of
          waiting for the next periodic refresh.

        Failure of any individual query is logged at DEBUG and skipped — power
        is best-effort data and shouldn't break the main refresh.
        """
        if user_device_id is not None:
            plug_ids = [user_device_id]
        else:
            plug_ids = [
                dev["userDeviceId"]
                for dev in self._devices.values()
                if dev.get("type") == DEVICE_TYPE_PLUG
                and dev.get("cache", {}).get("isOnline") is not False
            ]
        if not plug_ids:
            return
        updated = False
        for udid in plug_ids:
            try:
                data = await self.api.async_query_device(
                    udid, ACTION_QUERY_DISCONNECTOR, home_id=self._home_id
                )
            except ZhisuanApiError as err:
                _LOGGER.debug(
                    "QueryDisconnector failed for device %s: %s", udid, err
                )
                continue
            # data.deviceInfo.data = "47.0"（字符串形式的瓦数）
            device_info = data.get("deviceInfo") or {}
            raw = device_info.get("data")
            if raw is None:
                _LOGGER.debug(
                    "QueryDisconnector for device %s returned no .data (keys=%s)",
                    udid, list(device_info.keys()),
                )
                continue
            try:
                watts = float(raw)
            except (TypeError, ValueError):
                _LOGGER.debug(
                    "QueryDisconnector for device %s returned non-numeric: %r",
                    udid, raw,
                )
                continue
            self._plug_power[udid] = watts
            _LOGGER.debug("Plug %s real-time power: %.1f W", udid, watts)
            updated = True
        _LOGGER.debug(
            "Plug power refresh: %d plug(s), updated=%s",
            len(plug_ids), updated,
        )
        # 触发 listener 更新（让 power sensor 显示新值）
        if updated:
            self.async_set_updated_data(self._devices)

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
