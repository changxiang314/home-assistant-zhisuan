"""Light platform for ZhiSuan (挚算智联).

Capabilities are driven by the device's `actionList`:
- ``TurnOn``/``TurnOff``     → on/off
- ``SetBrightness``          → brightness (挚算 0-100 → HA 0-255)
- ``SetColorTemperature``    → color_temp (挚算 0-100 → HA kelvin)
- ``SetColor``               → RGB color
- ``SetBrightnessColorTemperature`` → 组合调光+调色（部分设备支持）

控制 API 关键点（实测 + 文档交叉确认）：
- 每个属性是**独立的 action name**，不是塞 extension 给 TurnOn：
  * ``SetBrightness``               name=name + ``extension.brightness``
  * ``SetColorTemperature``         name=name + ``extension.value`` (注意是 value 不是 colorTemperature)
  * ``SetBrightnessColorTemperature``  name=name + ``extension.brightness`` + ``extension.colorTemperature``
  * ``SetColor``                    name=name + ``extension.Red/Green/Blue`` (实测首字母大写，跟文档 red/green/blue 不一致)
- 设备状态字段（读取用）跟控制字段（写入用）名字不一样：
  * 状态：``extension.brightness``、``extension.colorTemperature``、``extension.color = {Red, Green, Blue}``
  * 控制：SetBrightness→brightness, SetColorTemperature→value, SetColor→Red/Green/Blue

NOTE on color_temp:
    挚算 API 文档定义 colorTemperature 为 0-100 的百分比，没有提供
    K（开尔文）或 mireds 映射。HA 要求 kelvin（开尔文）。

    实测语义（user v1.2.8 反馈）：
    - value=0  →  暖光 2700K（橙黄）
    - value=100 → 冷光 6500K（白蓝）

    这跟多数智能灯相反。改映射前请按实际设备重新校准。
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
    ACTION_SET_BRIGHTNESS_COLOR_TEMP,
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

# 挚算 colorTemperature 字段语义（实测）：
# value=0   → 暖光 2700K（橙黄）
# value=100 → 冷光 6500K（白蓝）
# 跟多数智能灯相反。
_COLOR_TEMP_KELVIN_MIN = 2700  # value=0 时
_COLOR_TEMP_KELVIN_MAX = 6500  # value=100 时


def _pct_to_kelvin(pct: int | float) -> int:
    # pct=0 → 2700K（暖）, pct=100 → 6500K（冷）
    return int(
        _COLOR_TEMP_KELVIN_MIN
        + pct / 100.0 * (_COLOR_TEMP_KELVIN_MAX - _COLOR_TEMP_KELVIN_MIN)
    )


def _kelvin_to_pct(k: int) -> int:
    # k=2700 → 0（暖）, k=6500 → 100（冷）
    pct = (k - _COLOR_TEMP_KELVIN_MIN) / (
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
        # 实测挚算 color 字段是 {"Red": N, "Green": N, "Blue": N}（首字母大写）
        color = self.ext.get(EXT_COLOR)
        if not isinstance(color, dict):
            return None
        r = color.get("Red") or color.get("red") or color.get("r")
        g = color.get("Green") or color.get("green") or color.get("g")
        b = color.get("Blue") or color.get("blue") or color.get("b")
        if r is not None and g is not None and b is not None:
            return (int(r), int(g), int(b))
        # 兜底：hue/saturation（挚算 hue 范围 0-360，sat 0-100）
        h = color.get("hue") or color.get("Hue") or color.get("h")
        s = color.get("saturation") or color.get("Saturation") or color.get("s")
        if h is not None and s is not None:
            from colorsys import hls_to_rgb

            rr, gg, bb = hls_to_rgb(float(h) / 360.0, 0.5, float(s) / 100.0)
            return (int(rr * 255), int(gg * 255), int(bb * 255))
        return None

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on with optional brightness / color_temp / color.

        挚算 API：每个属性是**独立的 action**，字段名也跟状态字段不一样。
        按以下优先级选 action：
        1. 调颜色（has color）         → SetColor
        2. 同时调光+调色（has both）  → SetBrightnessColorTemperature
        3. 调色温（has temp）         → SetColorTemperature
        4. 调亮度（has brightness）   → SetBrightness
        5. 纯开关                      → TurnOn
        """
        has_bright = (
            ATTR_BRIGHTNESS in kwargs
            and ACTION_SET_BRIGHTNESS in self._actions
        )
        has_temp = (
            ATTR_COLOR_TEMP_KELVIN in kwargs
            and ACTION_SET_COLOR_TEMP in self._actions
        )
        has_color = (
            ATTR_RGB_COLOR in kwargs
            and ACTION_SET_COLOR in self._actions
        )

        ext: dict[str, Any] = {}
        if has_color:
            # 调颜色（挚算 SetColor 的 extension 字段是小写 red/green/blue，范围 0-255。
            # 核对来源：官方 zs-openapi CLI actions.py + 服务端 DxDeviceControlServiceImpl.java。
            # 注意不要写成 Red/Green/Blue 大写 —— 服务端找不到 red 参数会返回 421。）
            action = ACTION_SET_COLOR
            r, g, b = kwargs[ATTR_RGB_COLOR]
            ext["red"] = int(r)
            ext["green"] = int(g)
            ext["blue"] = int(b)
        elif has_bright and has_temp and (
            ACTION_SET_BRIGHTNESS_COLOR_TEMP in self._actions
        ):
            # 同时调光 + 调色温（部分设备支持，extension 用 brightness + colorTemperature）
            action = ACTION_SET_BRIGHTNESS_COLOR_TEMP
            ext["brightness"] = int(
                round(kwargs[ATTR_BRIGHTNESS] / 255.0 * 100)
            )
            ext["colorTemperature"] = _kelvin_to_pct(
                int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            )
        elif has_temp:
            # 调色温（注意 extension 字段是 value，不是 colorTemperature）
            action = ACTION_SET_COLOR_TEMP
            ext["value"] = _kelvin_to_pct(
                int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            )
        elif has_bright:
            # 调亮度
            action = ACTION_SET_BRIGHTNESS
            ext["brightness"] = int(
                round(kwargs[ATTR_BRIGHTNESS] / 255.0 * 100)
            )
        else:
            # 纯开关
            action = ACTION_TURN_ON

        await self.coordinator.async_control(
            self._user_device_id, action, extension=ext or None
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )
