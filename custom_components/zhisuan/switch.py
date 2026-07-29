"""Switch platform for ZhiSuan (挚算智联)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .const import (
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    DEVICE_TYPE_AIR_FRESHER,
    DEVICE_TYPE_MIX_LIGHT_TOUCH_PANEL,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SCENE_TRIGGER,
    DEVICE_TYPE_SWITCH,
    DOMAIN,
    EXT_TURN_ON_OFF,
)
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

# 这些 type 的设备走 switch 平台
SWITCH_DEVICE_TYPES = {
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SCENE_TRIGGER,            # 四路快捷面板
    DEVICE_TYPE_MIX_LIGHT_TOUCH_PANEL,   # 三路自定义面板
    DEVICE_TYPE_AIR_FRESHER,             # 新风（暂用 switch，后续加 fan 平台）
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan switch entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[ZhisuanSwitchEntity] = []
    for user_device_id, device in coordinator.devices.items():
        if device.get("type") not in SWITCH_DEVICE_TYPES:
            continue
        # 多路设备（isVirtual=true）：每个 nodeId 一个实体
        if (device.get("trait") or {}).get("isVirtual"):
            # nodeId 列表在第一次拉的时候不一定全；保守起见至少建一个
            node_id = str(device.get("nodeId") or "1")
            entities.append(
                ZhisuanSwitchEntity(coordinator, user_device_id, node_id=node_id)
            )
        else:
            entities.append(ZhisuanSwitchEntity(coordinator, user_device_id))

    async_add_entities(entities)


class ZhisuanSwitchEntity(ZhisuanEntity, SwitchEntity):
    """A ZhiSuan switch (smart switch / plug)."""

    entity_description = SwitchEntityDescription(
        key="zhisuan_switch",
    )

    @property
    def is_on(self) -> bool | None:
        val = self.ext.get(EXT_TURN_ON_OFF)
        if val is None:
            return None
        return bool(val)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_ON
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )
