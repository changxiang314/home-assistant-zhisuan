"""Light platform for ZhiSuan (挚算智联).

Capabilities are driven by the device's `actionList`:
- ``TurnOn``/``TurnOff``     → on/off
- ``SetBrightness``          → brightness (挚算 0-100 → HA 0-255)
- ``SetColorTemperature``    → color_temp (挚算 0-100 → HA mireds)
- ``SetColor``               → RGB color (挚算 color object 格式 # 待验证)

NOTE on color_temp:
    挚算 API 文档定义 colorTemperature 为 0-100 的百分比，没有提供
    K（开尔文）或 mireds 映射。HA 要求 mireds（micro reciprocal degrees）。
    我们按"0 → 冷光（6500K），100 → 暖光（2700K）"做线性插值。
    实际设备可能不同 — 如有偏差请在校验设备后调整 _COLOR_TEMP_MIREDS_MIN/MAX。
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTION_SET_BRIGHTNESS,
    ACTION_SET_COLOR,
    ACTION_SET_COLOR_TEMP,
    ACTION_TURN_OFF,
    ACTION_TURN_ON,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_LIGHT,
    DOMAIN,
    EXT_BRIGHTNESS,
    EXT_COLOR,
    EXT_COLOR_TEMP,
    EXT_TURN_ON_OFF,
)
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

LIGHT_DEVICE_TYPES = {DEVICE_TYPE_LIGHT, DEVICE_TYPE_DIMMER}

# 挚算 colorTemperature 是 0-100 百分比。
# 假设：0 = 冷光 6500K，100 = 暖光 2700K（多数智能灯的常见映射）。
# 调宽/调窄请根据实际设备调。
_COLOR_TEMP_KELVIN_MAX = 6500  # 0% 时
_COLOR_TEMP_KELVIN_MIN = 2700  # 100% 时


def _pct_to_kelvin(pct: int | float) -> int:
    return int(
        _COLOR_TEMP_KELVIN_MAX
        - pct / 100.0 * (_COLOR_TEMP_KELVIN_MAX - _COLOR_TEMP_KELVIN_MIN)
    )


def _kelvin_to_pct(k: int) -> int:
    pct = (_COLOR_TEMP_KELVIN_MAX - k) / (
        _COLOR_TEMP_KELVIN_MAX - _COLOR_TEMP_KELVIN_MIN
    ) * 100
    return max(0, min(100, int(round(pct))))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan light entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[ZhisuanLightEntity] = []
    for user_device_id, device in coordinator.devices.items():
        if device.get("type") not in LIGHT_DEVICE_TYPES:
            continue
        node_id = None
        if (device.get("trait") or {}).get("isVirtual"):
            node_id = str(device.get("nodeId") or "1")
        entities.append(
            ZhisuanLightEntity(coordinator, user_device_id, node_id=node_id)
        )

    async_add_entities(entities)


class ZhisuanLightEntity(ZhisuanEntity, LightEntity):
    """A ZhiSuan light (开关型 / 调光 / 调色 / 调色温)."""

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
        node_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, user_device_id, node_id=node_id)
        device = self.device or {}
        actions: set[str] = set(device.get("actionList") or [])
        # 决定 HA color_mode
        if ACTION_SET_COLOR in actions:
            self._attr_color_mode = ColorMode.RGB
            self._attr_supported_color_modes = {ColorMode.RGB}
        elif ACTION_SET_COLOR_TEMP in actions:
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
        elif ACTION_SET_BRIGHTNESS in actions:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

        self._attr_name = None  # 用 device name
        self._actions = actions

    # ------------------------------------------------------------------
    # 状态读取
    # ------------------------------------------------------------------
    @property
    def is_on(self) -> bool | None:
        val = self.ext.get(EXT_TURN_ON_OFF)
        if val is None:
            # 数据缺失时返回 False（不是 None），
            # 否则 HA 会把 entity 标 unknown/unavailable
            return False
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    @property
    def brightness(self) -> int | None:
        if ACTION_SET_BRIGHTNESS not in self._actions:
            return None
        pct = self.ext.get(EXT_BRIGHTNESS)
        if pct is None:
            return None
        # 0-100 → 0-255
        return max(1, min(255, int(round(pct / 100.0 * 255))))

    @property
    def color_temp_kelvin(self) -> int | None:
        if ACTION_SET_COLOR_TEMP not in self._actions:
            return None
        pct = self.ext.get(EXT_COLOR_TEMP)
        if pct is None:
            return None
        return _pct_to_kelvin(pct)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if ACTION_SET_COLOR not in self._actions:
            return None
        # 挚算 color 字段是 Object，格式 # 待验证。
        # 优先按 {"r","g","b"}（0-255）解析；不行再考虑 {"hue","saturation"}。
        color = self.ext.get(EXT_COLOR)
        if not isinstance(color, dict):
            return None
        r = color.get("r")
        g = color.get("g")
        b = color.get("b")
        if r is not None and g is not None and b is not None:
            return (int(r), int(g), int(b))
        # 兜底：hue/saturation（挚算 hue 范围 0-360，sat 0-100）
        h = color.get("hue") or color.get("h")
        s = color.get("saturation") or color.get("s")
        if h is not None and s is not None:
            from colorsys import hls_to_rgb

            r, g, b = hls_to_rgb(float(h) / 360.0, 0.5, float(s) / 100.0)
            return (int(r * 255), int(g * 255), int(b * 255))
        return None

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        ext: dict[str, Any] = {}
        if ATTR_BRIGHTNESS in kwargs and ACTION_SET_BRIGHTNESS in self._actions:
            ext["brightness"] = int(round(kwargs[ATTR_BRIGHTNESS] / 255.0 * 100))
        if ATTR_COLOR_TEMP_KELVIN in kwargs and ACTION_SET_COLOR_TEMP in self._actions:
            ext["colorTemperature"] = _kelvin_to_pct(
                int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            )
        if ATTR_RGB_COLOR in kwargs and ACTION_SET_COLOR in self._actions:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            ext["color"] = {"r": int(r), "g": int(g), "b": int(b)}
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_ON, extension=ext or None
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )
