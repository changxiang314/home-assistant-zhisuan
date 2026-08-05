"""Climate platform for ZhiSuan (挚算智联).

Supports: AirCondition, AirConditionManager, FloorHeating, Infrared(AC),
MultiInOneManager/Panel (暖通多合一 / 地暖多合一 下的虚拟子设备).

# 待验证
挚算文档明确说明"模式值因设备而异"：
- AC: 1=COOL 2=HEAT 3=FAN（缺 DRY/AUTO 的定义 — 多数空调 DRY=4, AUTO=5）
- Light: 1-12（场景模式，跟 climate 无关）
默认映射按 AC 的常见数值；如果设备不一样请在 Options 里改。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

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
    ACTION_SET_SWING,
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

# ----- 父设备 ID → schema 类型 -----
# 2879 (MultiInOneManager 3210) 下的虚拟子设备用虚拟 schema（on/mode/setTemp/curTemp/speed）
# 2998 (MultiInOnePanel 3211) 下的子设备用 mix schema（turnOnOff/workMode/temperature/currentTemperature/windSpeed）
VIRTUAL_PARENT_IDS: Final = frozenset({2879})
MIX_PARENT_IDS: Final = frozenset({2998})

# 默认模式映射（HA 模式 → 挚算 mode 数值）
# v1.3.2 实测 user 设备 2999 (model virtual_AC_3in1_mix#2)：
#   mode=1 → HEAT，mode=2 → COOL
# 跟 PDF 文档 7.5 节写的"1=COLE 2=HEAT 3=FAN"相反（COLE 大概率是 COOL typo，
# 但 user 设备实际数值映射跟文档写的不一样）。
# 1↔2 对调是经验值，其他设备的 mode 数值可能也不一致——如果你的设备
# 调模式后实际跑的不对，请把这条反馈给我加进 per-device override。
DEFAULT_MODE_MAP: dict[HVACMode, int] = {
    HVACMode.COOL: 2,    # 文档说 1，但实测 2
    HVACMode.HEAT: 1,    # 文档说 2，但实测 1
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

# ----- 虚拟子设备 schema（MultiInOneManager 3210 下，model 包含 virtual_AC_3in1） -----
# extension 字段名不同，且子设备 state 通常为空 → 状态从父设备 eps[] 数组读
VIRTUAL_FIELD_NAMES: Final[dict[str, str]] = {
    "on": "on",
    "mode": "mode",
    "set_temp": "setTemp",
    "cur_temp": "curTemp",
    "speed": "speed",
    "node_id": "nodeId",
}
# 字符串 mode 名称 → HA 模式
VIRTUAL_MODE_REVERSE: Final[dict[str, HVACMode]] = {
    "COLD": HVACMode.COOL,
    "HOT": HVACMode.HEAT,
    "FAN": HVACMode.FAN_ONLY,
    "DRY": HVACMode.DRY,
    "AUTO": HVACMode.AUTO,
    "WIND": HVACMode.FAN_ONLY,  # 备用别名
}
VIRTUAL_MODE_MAP: Final[dict[HVACMode, int]] = {
    HVACMode.COOL: 1,
    HVACMode.HEAT: 2,
    HVACMode.FAN_ONLY: 3,
    HVACMode.DRY: 4,
    HVACMode.AUTO: 5,
}
# 字符串 speed 名称 → 数值
VIRTUAL_SPEED_REVERSE: Final[dict[str, int]] = {
    "HIGH": 4,
    "MID": 3,
    "MIDDLE": 3,
    "LOW": 2,
    "AUTO": 0,
}
VIRTUAL_SPEED_MAP: Final[dict[str, int]] = {
    "auto": 0,
    "low": 2,
    "medium": 3,
    "high": 4,
}

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

    _LOGGER.warning(
        "Climate setup: coordinator has %d devices, looking for types %s",
        len(coordinator.devices), CLIMATE_DEVICE_TYPES,
    )
    try:
        entities: list[ZhisuanClimateEntity] = []
        for i, (user_device_id, device) in enumerate(coordinator.devices.items()):
            dtype = device.get("type")
            if dtype not in CLIMATE_DEVICE_TYPES:
                continue
            _LOGGER.warning(
                "  Climate [%d] creating entity for device=%s type=%s name=%s",
                i, user_device_id, dtype, device.get("deviceName"),
            )
            try:
                node_id = None
                if (device.get("trait") or {}).get("isVirtual"):
                    node_id = str(device.get("nodeId") or "1")
                ent = ZhisuanClimateEntity(coordinator, user_device_id, node_id=node_id)
                entities.append(ent)
                _LOGGER.warning("    OK device=%s", user_device_id)
            except Exception as inner_exc:  # noqa: BLE001
                _LOGGER.exception(
                    "    FAIL device=%s type=%s: %s",
                    user_device_id, dtype, inner_exc,
                )
                raise
        _LOGGER.warning(
            "Climate: creating %d entities (matched types in devices: %s)",
            len(entities),
            sorted({d.get("type") for d in coordinator.devices.values()
                    if d.get("type") in CLIMATE_DEVICE_TYPES}),
        )
        async_add_entities(entities)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Climate platform setup failed")
        raise


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
        # schema 判定：按父设备 ID 区分虚拟/Mix
        parent_id = device.get("parentId")
        self._is_virtual_subdevice = parent_id in VIRTUAL_PARENT_IDS
        self._is_mix_subdevice = parent_id in MIX_PARENT_IDS
        actions: set[str] = set(device.get("actionList") or [])
        self._actions = actions

        features = ClimateEntityFeature(0)
        if ACTION_TURN_ON in actions and ACTION_TURN_OFF in actions:
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if ACTION_SET_TEMPERATURE in actions:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        # 模式切换：HA 2026 不用 MODE feature flag 了，只要 _attr_hvac_modes
        # 列表里 >1 个 mode 就会自动有模式选择 UI
        if ACTION_SET_MODE in actions and not self._is_infrared:
            pass  # mode capability comes from hvac_modes list below
        if ACTION_SET_WIND_SPEED in actions or (
            ACTION_SET_MODE in actions and self._is_infrared
        ):
            features |= ClimateEntityFeature.FAN_MODE
        if ACTION_SET_SWING in actions:
            features |= ClimateEntityFeature.SWING_MODE
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
        # SetSwing 是二元开关（PDF §7.12：mode=1 开 / mode=0 关，无中间值）
        # HA 2026 climate 没单独的 swing on/off 概念，用 preset_mode 兜底
        self._attr_swing_modes = ["off", "on"]

    # ------------------------------------------------------------------
    # 状态读取：按 schema 选字段
    # ------------------------------------------------------------------
    def _virtual_state(self) -> dict[str, Any]:
        """从父设备 MultiInOneManager 的 eps[] 数组读虚拟子设备状态。

        虚拟子设备的自身 extension 通常是空的（要控制一次才填充），
        父设备的 eps 数组里 epNum 对应子设备的 nodeId。
        """
        device = self.device or {}
        parent_id = device.get("parentId")
        if not parent_id:
            return {}
        parent = self.coordinator.get_device(parent_id) if hasattr(self.coordinator, "get_device") else None
        if not parent:
            return {}
        ext = (parent.get("cache") or {}).get("extension") or {}
        eps = ext.get("eps") or []
        my_node = device.get("nodeId") or self._node_id
        for ep in eps:
            if str(ep.get("epNum")) == str(my_node):
                return ep
        return {}

    def _read_on(self) -> bool | None:
        if self._is_virtual_subdevice:
            ext = self._virtual_state()
            if "on" in ext:
                return bool(ext["on"])
            # 兜底：自己 ext 里的 on（控制一次后会填充）
            v = self.ext.get("on")
            return bool(v) if v is not None else None
        # mix schema
        v = self.ext.get("turnOnOff")
        if v is None:
            return None
        if isinstance(v, str):
            return v.lower() == "true"
        return bool(v)

    def _read_mode(self) -> HVACMode | None:
        if self._is_virtual_subdevice:
            ext = self._virtual_state()
            m = ext.get("mode") or self.ext.get("mode")
            if m is None:
                return None
            return VIRTUAL_MODE_REVERSE.get(str(m).upper())
        # mix schema
        m = self.ext.get("workMode")
        if m is None:
            return None
        try:
            return DEFAULT_MODE_REVERSE.get(int(m))
        except (TypeError, ValueError):
            return None

    def _read_set_temp(self) -> float | None:
        if self._is_virtual_subdevice:
            ext = self._virtual_state()
            t = ext.get("setTemp")
            if t is None:
                t = self.ext.get("setTemp")
            try:
                return float(t) if t is not None else None
            except (TypeError, ValueError):
                return None
        # mix schema
        t = self.ext.get("temperature")
        if t is None:
            return None
        try:
            return float(t)
        except (TypeError, ValueError):
            return None

    def _read_cur_temp(self) -> float | None:
        if self._is_virtual_subdevice:
            ext = self._virtual_state()
            t = ext.get("curTemp")
            if t is None:
                t = self.ext.get("curTemp")
            try:
                return float(t) if t is not None else None
            except (TypeError, ValueError):
                return None
        # mix schema
        t = self.ext.get("currentTemperature")
        if t is None:
            return None
        try:
            return float(t)
        except (TypeError, ValueError):
            return None

    def _read_fan_speed(self) -> str | None:
        if self._is_virtual_subdevice:
            ext = self._virtual_state()
            s = ext.get("speed") or self.ext.get("speed")
            if s is None:
                return None
            # 字符串 "HIGH"/"MID"/"LOW" → 数值 4/3/2
            rev_int = VIRTUAL_SPEED_REVERSE.get(str(s).upper())
            if rev_int is None:
                return None
            return WIND_SPEED_REVERSE.get(rev_int)
        # mix schema
        ws = self.ext.get("windSpeed")
        if ws is None:
            return None
        try:
            return WIND_SPEED_REVERSE.get(int(ws))
        except (TypeError, ValueError):
            return None

    @property
    def hvac_mode(self) -> HVACMode | None:
        on = self._read_on()
        if on is False:
            return HVACMode.OFF
        if self._is_infrared:
            return HVACMode.FAN_ONLY
        mode = self._read_mode()
        if on is True and mode is None:
            return None  # 已开但模式未知
        return mode

    @property
    def current_temperature(self) -> float | None:
        return self._read_cur_temp()

    @property
    def target_temperature(self) -> float | None:
        return self._read_set_temp()

    @property
    def fan_mode(self) -> str | None:
        return self._read_fan_speed()

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
            _LOGGER.debug(
                "climate[%s] set_hvac_mode -> OFF", self._user_device_id,
            )
            await self.coordinator.async_control(
                self._user_device_id, ACTION_TURN_OFF
            )
            return
        # 先开（用 _read_on 兼容两种 schema）
        was_off = self._read_on() is False
        if was_off:
            _LOGGER.debug(
                "climate[%s] set_hvac_mode: device off, sending TurnOn first",
                self._user_device_id,
            )
            await self.coordinator.async_control(
                self._user_device_id, ACTION_TURN_ON
            )
            # 防 race：设备开机未完成时 SetMode 会被丢
            await asyncio.sleep(0.5)
        # 设模式
        if ACTION_SET_MODE not in self._actions:
            _LOGGER.debug(
                "climate[%s] SetMode not in actionList, skipping",
                self._user_device_id,
            )
            return
        # 两种 schema 都用 SetMode + {"mode": int}（按文档 §7.5）
        mode_int = DEFAULT_MODE_MAP.get(hvac_mode)
        if mode_int is None:
            _LOGGER.warning(
                "climate[%s] unsupported hvac_mode: %s",
                self._user_device_id, hvac_mode,
            )
            return
        _LOGGER.debug(
            "climate[%s] set_hvac_mode %s -> SetMode mode=%s (was_off=%s)",
            self._user_device_id, hvac_mode, mode_int, was_off,
        )
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_MODE,
            extension={"mode": mode_int},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get("temperature")
        if temp is None or ACTION_SET_TEMPERATURE not in self._actions:
            return
        # 挚算 API: SetTemperature 的 extension 键是 "value"（不是 "temperature"）
        # 来源: zs_cli/commands/openapi/actions.py 第 21 行注释
        _LOGGER.debug(
            "climate[%s] set_temperature=%s", self._user_device_id, temp,
        )
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_TEMPERATURE,
            extension={"value": float(temp)},
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if ACTION_SET_WIND_SPEED not in self._actions:
            _LOGGER.debug(
                "climate[%s] SetWindSpeed not in actionList, fan_mode=%s ignored",
                self._user_device_id, fan_mode,
            )
            return
        speed = WIND_SPEED_MAP.get(fan_mode)
        if speed is None:
            _LOGGER.warning(
                "climate[%s] unknown fan_mode: %s",
                self._user_device_id, fan_mode,
            )
            return
        # 风速不能开设备（红外无 wind speed 跟此逻辑不同）
        # 但 mix schema 设备关时直接发 SetWindSpeed 会被设备忽略
        # 这里给个保险：如果关着，先 TurnOn 再等 0.5s
        was_off = self._read_on() is False
        if was_off and not self._is_infrared:
            _LOGGER.debug(
                "climate[%s] set_fan_mode: device off, sending TurnOn first",
                self._user_device_id,
            )
            await self.coordinator.async_control(
                self._user_device_id, ACTION_TURN_ON
            )
            await asyncio.sleep(0.5)
        _LOGGER.debug(
            "climate[%s] set_fan_mode %s -> SetWindSpeed value=%s (was_off=%s)",
            self._user_device_id, fan_mode, speed, was_off,
        )
        # 挚算 API: SetWindSpeed 的 extension 键也是 "value"
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_WIND_SPEED,
            extension={"value": speed},
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if ACTION_SET_SWING not in self._actions:
            return
        # swing_mode 是 "off" 或 "on"（HA 标准值）
        # 挚算 SetSwing 文档：mode=1 开 / mode=0 关
        swing_int = 1 if swing_mode == "on" else 0
        _LOGGER.debug(
            "climate[%s] set_swing_mode %s -> SetSwing mode=%s",
            self._user_device_id, swing_mode, swing_int,
        )
        await self.coordinator.async_control(
            self._user_device_id,
            ACTION_SET_SWING,
            extension={"mode": swing_int},
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_ON
        )

    async def async_turn_off(self) -> None:
        await self.coordinator.async_control(
            self._user_device_id, ACTION_TURN_OFF
        )
