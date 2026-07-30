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
        """Read isOnline from various possible locations in the device payload.

        挚算 API 不一致：isOnline 有时在 cache 顶层，有时在 cache.extension 里，
        有时缺失。三种位置都查一下，缺失就认为在线。
        """
        d = self.device or {}
        cache = d.get("cache") or {}
        # 1) cache.isOnline（最常见）
        val = cache.get("isOnline")
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() == "true"
        # 2) cache.extension.isOnline
        ext = cache.get("extension") or {}
        if isinstance(ext, dict):
            val = ext.get("isOnline")
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() == "true"
        # 3) 字段缺失 → 视为在线（避免误报）
        return True

    @property
    def available(self) -> bool:
        # 只要求设备在 coordinator 缓存里 + coordinator 上次更新成功。
        # is_online 单独通过 extra_state_attributes 暴露，不影响可用性。
        return super().available and self.device is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose online status + raw device id for debugging."""
        if not self.device:
            return None
        attrs: dict[str, Any] = {
            "user_device_id": self._user_device_id,
            "online": self.is_online,
        }
        node_id = self._node_id or self.device.get("nodeId")
        if node_id is not None:
            attrs["node_id"] = str(node_id)
        # 如果 cache.extension 是空的（state 同步不到云端），标注
        ext = (self.device.get("cache") or {}).get("extension") or {}
        if not ext:
            attrs["state_sync_warning"] = (
                "挚算云未同步此设备状态，控制可能有效但状态不会回显"
            )
        return attrs

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
        # 挚算设备是子设备的（如 AC/地暖挂在 gateway 下面），
        # 用 via_device 链接到父设备，HA 才不会丢
        parent_id = d.get("parentId")
        if parent_id:
            info_kwargs["via_device"] = (DOMAIN, str(parent_id))
        return DeviceInfo(**info_kwargs)

    @property
    def unique_id_suffix(self) -> str:
        if self._node_id:
            return f"_{self._node_id}"
        return ""

    @property
    def unique_id(self) -> str:
        return f"{self._user_device_id}{self.unique_id_suffix}"
