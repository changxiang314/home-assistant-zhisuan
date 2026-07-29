"""Cover (curtain) platform for ZhiSuan (挚算智联)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTION_PAUSE,
    ACTION_SET_POSITION,
    ACTION_SET_REVERSE,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    CURTAIN_OPERATION_CLOSED,
    CURTAIN_OPERATION_OPEN,
    CURTAIN_OPERATION_STOPPED,
    DEVICE_TYPE_CURTAINS,
    DEVICE_TYPE_CURTAINS_MOTOR,
    DOMAIN,
    EXT_OPERATION_MODE,
    EXT_POSITION,
)
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

COVER_DEVICE_TYPES = {DEVICE_TYPE_CURTAINS, DEVICE_TYPE_CURTAINS_MOTOR}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan cover entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[ZhisuanCoverEntity] = []
    for user_device_id, device in coordinator.devices.items():
        if device.get("type") not in COVER_DEVICE_TYPES:
            continue
        node_id = None
        if (device.get("trait") or {}).get("isVirtual"):
            node_id = str(device.get("nodeId") or "1")
        entities.append(
            ZhisuanCoverEntity(coordinator, user_device_id, node_id=node_id)
        )
    async_add_entities(entities)


class ZhisuanCoverEntity(ZhisuanEntity, CoverEntity):
    """A ZhiSuan curtain / curtain motor."""

    _attr_name = None

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
        node_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, user_device_id, node_id=node_id)
        actions: set[str] = set((self.device or {}).get("actionList") or [])
        features = CoverEntityFeature(0)
        if ACTION_TURN_ON in actions:
            features |= CoverEntityFeature.OPEN
        if ACTION_TURN_OFF in actions:
            features |= CoverEntityFeature.CLOSE
        if ACTION_PAUSE in actions:
            features |= CoverEntityFeature.STOP
        if ACTION_SET_POSITION in actions:
            features |= CoverEntityFeature.SET_POSITION
        if ACTION_SET_REVERSE in actions:
            # 挚算有反转接口 — 我们不暴露为标准 cover 特性，作为服务调
            self._attr_extra_state_attributes = {"supports_reverse": True}
        # 至少要有 open/close
        if not (features & (CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE)):
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        self._attr_supported_features = features
        self._actions = actions

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def current_cover_position(self) -> int | None:
        """挚算 position 0-100, 0=全关, 100=全开。直接给 HA。"""
        pos = self.ext.get(EXT_POSITION)
        if pos is None:
            return None
        return max(0, min(100, int(pos)))

    @property
    def is_opening(self) -> bool | None:
        op = self.ext.get(EXT_OPERATION_MODE)
        if op is None:
            return None
        # operationMode: 0关 1开 2停
        # HA 没有"运行中"概念；operationMode==1 但 position<100 时算"正在开"
        if op == CURTAIN_OPERATION_OPEN and (
            self.current_cover_position or 0
        ) < 100:
            return True
        return None

    @property
    def is_closing(self) -> bool | None:
        op = self.ext.get(EXT_OPERATION_MODE)
        if op is None:
            return None
        if op == CURTAIN_OPERATION_CLOSED and (
            self.current_cover_position or 0
        ) > 0:
            return True
        return None

    @property
    def is_closed(self) -> bool | None:
        op = self.ext.get(EXT_OPERATION_MODE)
        if op is None:
            return None
        if op == CURTAIN_OPERATION_CLOSED:
            return True
        if op == CURTAIN_OPERATION_OPEN:
            return False
        # 停止状态：按 position 推断
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_ON
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        if ACTION_PAUSE not in self._actions:
            return
        await self.coordinator.async_control(
            self._user_device_id, ACTION_PAUSE
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        if ACTION_SET_POSITION not in self._actions:
            return
        position = int(kwargs[ATTR_POSITION])
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_POSITION,
            extension={"position": position},
        )
