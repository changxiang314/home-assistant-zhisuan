"""Base entity for ZhiSuan (挚算智联) devices."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZhisuanCoordinator


class ZhisuanEntity(CoordinatorEntity[ZhisuanCoordinator]):
    """Base class for all ZhiSuan entities.

    Each entity represents either:
    - a whole device (switch, light, cover, climate), keyed by userDeviceId
    - a single property of a multi-property device (sensor), keyed by
      (userDeviceId, property_name)
    """

    _attr_has_entity_name = True
    _attr_should_poll = False  # 全靠 coordinator 推送

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
        node_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._user_device_id = user_device_id
        self._node_id = node_id

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    @property
    def device(self) -> dict[str, Any] | None:
        return self.coordinator.get_device(self._user_device_id)

    @property
    def device_name(self) -> str:
        d = self.device or {}
        return d.get("deviceName", f"设备 {self._user_device_id}")

    @property
    def ext(self) -> dict[str, Any]:
        """Shortcut to cache.extension, returns empty dict if unavailable."""
        d = self.device
        if not d:
            return {}
        cache = d.get("cache") or {}
        return cache.get("extension") or {}

    @property
    def is_online(self) -> bool:
        cache = (self.device or {}).get("cache") or {}
        # isOnline 是 String（"true"/"false"）在 cache 顶层，不在 extension
        val = cache.get("isOnline")
        if isinstance(val, str):
            return val.lower() == "true"
        if isinstance(val, bool):
            return val
        return True  # 没有数据时认为在线（避免误报 offline）

    @property
    def available(self) -> bool:
        return super().available and self.device is not None and self.is_online

    # ------------------------------------------------------------------
    # HA 通用属性
    # ------------------------------------------------------------------
    @property
    def device_info(self) -> DeviceInfo:
        d = self.device or {}
        room_id = d.get("roomId")
        room_name = (
            self.coordinator.get_room_name(room_id) if room_id else None
        ) or d.get("roomName")
        info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, str(self._user_device_id))},
            "manufacturer": MANUFACTURER,
            "model": d.get("model"),
            "name": self.device_name,
        }
        if room_name:
            info_kwargs["suggested_area"] = room_name
        if self._node_id:
            info_kwargs["serial_number"] = f"{d.get('deviceMac', '')}_{self._node_id}"
        elif d.get("deviceMac"):
            info_kwargs["serial_number"] = d["deviceMac"]
        return DeviceInfo(**info_kwargs)

    @property
    def unique_id_suffix(self) -> str:
        if self._node_id:
            return f"_{self._node_id}"
        return ""

    @property
    def unique_id(self) -> str:
        return f"{self._user_device_id}{self.unique_id_suffix}"
