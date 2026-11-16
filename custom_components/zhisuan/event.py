"""Event platform for ZhiSuan (挚算智联) — 6014 四路快捷面板按键事件.

6014 面板的 4 个键是"瞬时触发"（按下即发通知），不是开关，
所以用 HA 的 event 实体表达：每块面板 1 个实体、4 种 event_type
（button_1..button_4），自动化里用「事件触发」即可。

云端推送格式与去重见 ``push.py`` / ``docs/api-quirks.md`` §7.2。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_TYPE_SCENE_TRIGGER, DOMAIN
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity
from .push import SHORTCUT_PANEL_BUTTONS

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES: list[str] = [
    f"button_{i}" for i in range(1, SHORTCUT_PANEL_BUTTONS + 1)
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one button-event entity per 6014 shortcut panel."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = [
        ZhisuanPanelButtonEvent(coordinator, user_device_id)
        for user_device_id, device in coordinator.devices.items()
        if device.get("type") == DEVICE_TYPE_SCENE_TRIGGER
    ]
    async_add_entities(entities)


class ZhisuanPanelButtonEvent(ZhisuanEntity, EventEntity):
    """A 6014 four-button panel: one entity, event_type per physical button."""

    # 6014 在四个房间的设备名都叫「四路快捷面板」（云端 deviceName 不带房间），
    # 用 has_entity_name=True 会生成四个一模一样的 entity_id（只剩 _2/_3/_4 后缀）。
    # 所以这里把房间名写进实体名，得到 event.chu_fang_kuai_jie_mian_ban_an_jian
    # 这类可读的 id。代价是实体名里带区域，属于"设备名重复"下的有意取舍。
    _attr_has_entity_name = False
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
    ) -> None:
        super().__init__(coordinator, user_device_id)
        device = self.device or {}
        room = (
            coordinator.get_room_name(device.get("roomId"))
            or device.get("roomName")
            or ""
        )
        self._attr_name = f"{room}快捷面板按键" if room else "快捷面板按键"

    @property
    def unique_id(self) -> str:
        # 旧的 switch 实体 unique_id 就是 str(user_device_id)，
        # 这里加后缀避免和旧实体的迁移过程打架。
        return f"{self._user_device_id}_panel_button"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_register_button_press_listener(
                self._handle_press
            )
        )

    @callback
    def _handle_press(self, press: dict[str, Any]) -> None:
        if press["user_device_id"] != self._user_device_id:
            return
        self._trigger_event(
            f"button_{press['button']}",
            {
                "pressed_at": press.get("pressed_at"),
                "message_id": press.get("message_id"),
                "identify": press.get("identify"),
            },
        )
        self.async_write_ha_state()
