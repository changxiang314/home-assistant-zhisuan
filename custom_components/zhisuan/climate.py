"""Climate platform for ZhiSuan (挚算智联).

Supports: AirCondition, AirConditionManager, FloorHeating, Infrared(AC).

# 待验证
挚算文档明确说明"模式值因设备而异"：
- AC: 1=COOL 2=HEAT 3=FAN（缺 DRY/AUTO 的定义 — 多数空调 DRY=4, AUTO=5）
- Light: 1-12（场景模式，跟 climate 无关）
默认映射按 AC 的常见数值；如果设备不一样请在 Options 里改。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTION_SET_MODE,
    ACTION_SET_TEMPERATURE,
    ACTION_SET_WIND_SPEED,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    DEVICE_TYPE_AIR_CONDITION,
    DEVICE_TYPE_AIR_CONDITION_MANAGER,
    DEVICE_TYPE_FLOOR_HEATING,
    DEVICE_TYPE_INFRARED,
    DEVICE_TYPE_MULTI_IN_ONE_PANEL,
    DEVICE_TYPE_MULTI_IN_ONE_MANAGER,
    DOMAIN,
    EXT_MODE,
    EXT_TEMPERATURE,
    EXT_TURN_ON_OFF,
    EXT_WIND_SPEED,
)
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

CLIMATE_DEVICE_TYPES = {
    DEVICE_TYPE_AIR_CONDITION,
    DEVICE_TYPE_AIR_CONDITION_MANAGER,
    DEVICE_TYPE_FLOOR_HEATING,
    DEVICE_TYPE_INFRARED,
    DEVICE_TYPE_MULTI_IN_ONE_PANEL,
    DEVICE_TYPE_MULTI_IN_ONE_MANAGER,
}

# 默认模式映射（HA 模式 → 挚算 mode 数值）
# 这是按常见空调的常见映射；如不一致在 Options flow 里让用户调整
DEFAULT_MODE_MAP: dict[HVACMode, int] = {
    HVACMode.COOL: 1,
    HVACMode.HEAT: 2,
    HVACMode.FAN_ONLY: 3,
    HVACMode.DRY: 4,
    HVACMode.AUTO: 5,
    HVACMode.OFF: 0,
}

# 挚算 mode 数值 → HA 模式（反向映射）
DEFAULT_MODE_REVERSE: dict[int, HVACMode] = {v: k for k, v in DEFAULT_MODE_MAP.items()}

# 挚算 windSpeed 0-5: 0=Auto, 2=Low, 3=Mid, 4=High（按文档）
WIND_SPEED_MAP: dict[str, int] = {
    "auto": 0,
    "low": 2,
    "medium": 3,
    "high": 4,
}
WIND_SPEED_REVERSE: dict[int, str] = {v: k for k, v in WIND_SPEED_MAP.items()}

# 红外空调伴侣：状态从设备读不到
INFRARED_DEVICE_TYPES = {DEVICE_TYPE_INFRARED}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan climate entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[ZhisuanClimateEntity] = []
    for user_device_id, device in coordinator.devices.items():
        if device.get("type") not in CLIMATE_DEVICE_TYPES:
            continue
        node_id = None
        if (device.get("trait") or {}).get("isVirtual"):
            node_id = str(device.get("nodeId") or "1")
        entities.append(
            ZhisuanClimateEntity(coordinator, user_device_id, node_id=node_id)
        )
    async_add_entities(entities)


class ZhisuanClimateEntity(ZhisuanEntity, ClimateEntity):
    """A ZhiSuan air conditioner / floor heating / IR AC companion."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_name = None

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
        node_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, user_device_id, node_id=node_id)
        device = self.device or {}
        self._is_infrared = device.get("type") in INFRARED_DEVICE_TYPES
        actions: set[str] = set(device.get("actionList") or [])
        self._actions = actions

        features = ClimateEntityFeature(0)
        if ACTION_TURN_ON in actions and ACTION_TURN_OFF in actions:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if ACTION_SET_TEMPERATURE in actions:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if ACTION_SET_MODE in actions:
            # 支持的模式按 device type 决定
            if self._is_infrared:
                features |= ClimateEntityFeature.FAN_MODE
            else:
                features |= ClimateEntityFeature.MODE
                features |= ClimateEntityFeature.FAN_MODE
        if ACTION_SET_WIND_SPEED in actions:
            features |= ClimateEntityFeature.FAN_MODE
        self._attr_supported_features = features

        # 支持的 HA 模式
        if self._is_infrared:
            self._attr_hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
        else:
            self._attr_hvac_modes = [
                HVACMode.OFF,
                HVACMode.COOL,
                HVACMode.HEAT,
                HVACMode.FAN_ONLY,
                HVACMode.DRY,
                HVACMode.AUTO,
            ]
        self._attr_fan_modes = ["auto", "low", "medium", "high"]

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self.ext.get(EXT_TURN_ON_OFF):
            return HVACMode.OFF
        # 红外伴侣按 fan_mode 处理，hvac_mode 永远 FAN_ONLY
        if self._is_infrared:
            return HVACMode.FAN_ONLY
        mode = self.ext.get(EXT_MODE)
        if mode is None:
            return None
        return DEFAULT_MODE_REVERSE.get(int(mode))

    @property
    def current_temperature(self) -> float | None:
        # 文档有 temperature（目标）和 currentTemperature（实际）。先取实际
        cur = self.ext.get("currentTemperature")
        if cur is not None:
            try:
                return float(cur)
            except (TypeError, ValueError):
                return None
        return None

    @property
    def target_temperature(self) -> float | None:
        t = self.ext.get(EXT_TEMPERATURE)
        if t is None:
            return None
        try:
            return float(t)
        except (TypeError, ValueError):
            return None

    @property
    def fan_mode(self) -> str | None:
        ws = self.ext.get(EXT_WIND_SPEED)
        if ws is None:
            return None
        return WIND_SPEED_REVERSE.get(int(ws))

    @property
    def min_temp(self) -> float:
        return 16.0

    @property
    def max_temp(self) -> float:
        return 32.0

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_control(
                self._user_device_id, ACTION_TURN_OFF
            )
            return
        # 先开
        if not self.ext.get(EXT_TURN_ON_OFF):
            await self.coordinator.async_control(
                self._user_device_id, ACTION_TURN_ON
            )
        # 设模式
        if ACTION_SET_MODE not in self._actions:
            return
        mode_int = DEFAULT_MODE_MAP.get(hvac_mode)
        if mode_int is None:
            _LOGGER.warning("Unsupported hvac_mode: %s", hvac_mode)
            return
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_MODE,
            extension={"mode": mode_int},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None or ACTION_SET_TEMPERATURE not in self._actions:
            return
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_TEMPERATURE,
            extension={"temperature": float(temp)},
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if ACTION_SET_WIND_SPEED not in self._actions:
            return
        speed = WIND_SPEED_MAP.get(fan_mode)
        if speed is None:
            return
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_WIND_SPEED,
            extension={"windSpeed": speed},
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_ON
        )

    async def async_turn_off(self) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )
