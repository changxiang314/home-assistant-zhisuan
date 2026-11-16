#!/usr/bin/env python3
"""逻辑桩测试 —— 不启动 Home Assistant 也能验证实体行为。

为什么需要它：这颗 HA 跑在用户家里的小主机上，改一版要 HACS 更新 + 重启，
一轮 15 分钟。所以任何"能力声明 / 控制分发 / 字段名 / 数值映射"的改动都必须
先在这里跑通，再推给用户。

原理：造一套最小的 `homeassistant.*` 假模块塞进 sys.modules，然后把
custom_components/zhisuan 当成包加载，真实执行集成代码（不是复制逻辑）。

用法：
    python3 tools/test_logic.py          # 全部跑
    python3 tools/test_logic.py -v       # 显示每个断言

退出码 0 = 全过，1 = 有失败。可以直接挂 CI。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import types
from enum import Enum, IntFlag

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.join(HERE, "..", "custom_components", "zhisuan")

FAILS: list[str] = []
VERBOSE = False


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        if VERBOSE:
            print(f"  PASS  {label}" + (f"   {detail}" if detail else ""))
        return
    print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))
    FAILS.append(label)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# 1. 伪造 homeassistant 模块树
# ---------------------------------------------------------------------------
def build_ha_stubs() -> None:
    def mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    ha = mod("homeassistant")
    comps = mod("homeassistant.components")
    helpers = mod("homeassistant.helpers")

    # --- aiohttp ---
    # api.py 在模块顶层 import aiohttp。真装 aiohttp 会让这个测试多一个依赖，
    # 所以这里造一个最小桩：只有类型标注和异常类会被用到。
    aiohttp_mod = mod("aiohttp")

    class ClientError(Exception):
        pass

    class ClientResponseError(ClientError):
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    class ClientSession:
        pass

    class ClientConnectorError(ClientError):
        pass

    aiohttp_mod.ClientError = ClientError
    aiohttp_mod.ClientResponseError = ClientResponseError
    aiohttp_mod.ClientTimeout = ClientTimeout
    aiohttp_mod.ClientSession = ClientSession
    aiohttp_mod.ClientConnectorError = ClientConnectorError

    # --- homeassistant.components.light ---
    light_mod = mod("homeassistant.components.light")
    light_mod.ATTR_BRIGHTNESS = "brightness"
    light_mod.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light_mod.ATTR_RGB_COLOR = "rgb_color"

    class ColorMode(Enum):
        ONOFF = "onoff"
        BRIGHTNESS = "brightness"
        COLOR_TEMP = "color_temp"
        RGB = "rgb"
        RGBW = "rgbw"
        RGBWW = "rgbww"
        WHITE = "white"
        HS = "hs"
        XY = "xy"

    light_mod.ColorMode = ColorMode

    class LightEntity:
        # 与真实 HA 一致：color_mode / supported_color_modes 都是从 _attr_ 派生的
        # cached_property，子类可以用普通 property 覆盖（本集成就这么做）
        _attr_color_mode = None
        _attr_supported_color_modes = None
        _attr_min_color_temp_kelvin = 2000
        _attr_max_color_temp_kelvin = 6535
        _attr_name = None

        @property
        def color_mode(self):
            return self._attr_color_mode

        @property
        def supported_color_modes(self):
            return self._attr_supported_color_modes

    light_mod.LightEntity = LightEntity

    # --- homeassistant.components.climate ---
    climate_mod = mod("homeassistant.components.climate")

    class HVACMode(Enum):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        AUTO = "auto"
        DRY = "dry"
        FAN_ONLY = "fan_only"

    class HVACAction(Enum):
        COOLING = "cooling"
        DRYING = "drying"
        FAN = "fan"
        HEATING = "heating"
        IDLE = "idle"
        OFF = "off"

    class ClimateEntityFeature(IntFlag):
        # 数值与 HA 2026 一致（见 homeassistant/components/climate/const.py）
        TARGET_TEMPERATURE = 1
        TARGET_TEMPERATURE_RANGE = 2
        TARGET_HUMIDITY = 4
        FAN_MODE = 8
        PRESET_MODE = 16
        SWING_MODE = 32
        TURN_OFF = 128
        TURN_ON = 256
        SWING_HORIZONTAL_MODE = 512

    class ClimateEntity:
        _attr_supported_features = ClimateEntityFeature(0)
        _attr_hvac_modes = []
        _attr_hvac_mode = None
        _attr_fan_modes = None
        _attr_fan_mode = None
        _attr_swing_modes = None
        _attr_name = None

        @property
        def supported_features(self):
            return self._attr_supported_features

        @property
        def hvac_modes(self):
            return self._attr_hvac_modes

        @property
        def fan_modes(self):
            return self._attr_fan_modes

        @property
        def swing_modes(self):
            return self._attr_swing_modes

    climate_mod.HVACMode = HVACMode
    climate_mod.HVACAction = HVACAction
    climate_mod.ClimateEntityFeature = ClimateEntityFeature
    climate_mod.ClimateEntity = ClimateEntity

    # --- homeassistant.components.scene ---
    scene_mod = mod("homeassistant.components.scene")

    class Scene:
        """最小 Scene 桩。

        真实 HA 里 `Scene` 继承 RestoreEntity → Entity，
        集成只需实现 `async_activate()`。
        """

        _attr_should_poll = False
        _attr_name = None
        _attr_unique_id = None
        _attr_icon = None

        def __init__(self, *a, **k):
            pass

        @property
        def name(self):
            return self._attr_name

        @property
        def unique_id(self):
            return self._attr_unique_id

        @property
        def icon(self):
            return self._attr_icon

        async def async_added_to_hass(self):
            pass

        def async_on_remove(self, func):
            pass

        def async_write_ha_state(self):
            pass

    scene_mod.Scene = Scene

    # --- homeassistant.exceptions ---
    exc_mod = mod("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exc_mod.HomeAssistantError = HomeAssistantError

    # --- homeassistant.const ---
    const_mod = mod("homeassistant.const")

    class UnitOfTemperature(Enum):
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    const_mod.UnitOfTemperature = UnitOfTemperature
    const_mod.CONF_USERNAME = "username"
    const_mod.CONF_PASSWORD = "password"

    # --- homeassistant.config_entries ---
    ce = mod("homeassistant.config_entries")

    class ConfigEntry:
        pass

    ce.ConfigEntry = ConfigEntry

    # --- homeassistant.core ---
    core = mod("homeassistant.core")

    class HomeAssistant:
        pass

    core.HomeAssistant = HomeAssistant
    core.callback = lambda fn: fn

    # --- homeassistant.helpers.* ---
    uc = mod("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *a, **k):
            pass

        def __class_getitem__(cls, item):
            return cls

    class UpdateFailed(Exception):
        pass

    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        def __class_getitem__(cls, item):
            return cls

    uc.DataUpdateCoordinator = DataUpdateCoordinator
    uc.UpdateFailed = UpdateFailed
    uc.CoordinatorEntity = CoordinatorEntity

    dr = mod("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        def __init__(self, **kw):
            super().__init__(**kw)

    dr.DeviceInfo = DeviceInfo

    ep = mod("homeassistant.helpers.entity_platform")
    ep.AddEntitiesCallback = object

    # 把子模块挂到父模块上，`from homeassistant.helpers.x import y` 才走得通
    ha.components = comps
    ha.helpers = helpers
    ha.const = const_mod
    ha.config_entries = ce
    ha.core = core
    comps.light = light_mod
    comps.climate = climate_mod
    comps.scene = scene_mod
    helpers.update_coordinator = uc
    helpers.device_registry = dr
    helpers.entity_platform = ep
    ha.exceptions = exc_mod


# ---------------------------------------------------------------------------
# 2. 以包形式加载集成源码
# ---------------------------------------------------------------------------
def load_integration():
    pkg = types.ModuleType("zs")
    pkg.__path__ = [os.path.abspath(PKG_DIR)]
    sys.modules["zs"] = pkg

    def load(name: str) -> types.ModuleType:
        path = os.path.join(pkg.__path__[0], f"{name}.py")
        spec = importlib.util.spec_from_file_location(f"zs.{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"zs.{name}"] = module
        spec.loader.exec_module(module)
        setattr(pkg, name, module)
        return module

    # 依赖顺序：const → api → coordinator → entity → 各平台
    for name in ("const", "api", "coordinator", "entity", "light", "climate", "scene"):
        load(name)
    return pkg


# ---------------------------------------------------------------------------
# 3. 假 coordinator
# ---------------------------------------------------------------------------
class FakeCoordinator:
    def __init__(self, devices, scenes=None):
        self.devices = devices
        self.scenes = scenes or {}
        self.calls = []
        self.scene_calls = []
        self.last_update_success = True
        self.scene_sink = None
        self.listeners = []

    def get_device(self, uid):
        return self.devices.get(uid)

    def get_room_name(self, rid):
        return None

    def get_scene(self, scene_id):
        return self.scenes.get(scene_id)

    def set_scene_sink(self, sink):
        self.scene_sink = sink

    def async_add_listener(self, cb):
        self.listeners.append(cb)

        def _remove():
            self.listeners.remove(cb)

        return _remove

    async def async_activate_scene(self, scene_id):
        self.scene_calls.append(scene_id)

    async def async_control(self, uid, action, extension=None):
        self.calls.append({"uid": uid, "action": action, "extension": extension})

    async def async_refresh_plug_power(self, uid=None):
        pass

    def get_plug_power(self, uid):
        return None


def device(uid, name, dtype, actions, ext=None, model="", parent_id=None, node_id=None):
    dev = {
        "userDeviceId": uid,
        "deviceName": name,
        "type": dtype,
        "model": model,
        "actionList": actions,
        "cache": {"isOnline": True, "extension": ext or {}},
    }
    if parent_id is not None:
        dev["parentId"] = parent_id
    if node_id is not None:
        dev["nodeId"] = node_id
    return dev


# ---------------------------------------------------------------------------
# 4. 测试用例
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402


def run() -> None:
    build_ha_stubs()
    pkg = load_integration()

    light = pkg.light
    climate = pkg.climate
    const = pkg.const

    ColorMode = sys.modules["homeassistant.components.light"].ColorMode
    HVACMode = sys.modules["homeassistant.components.climate"].HVACMode
    ClimateEntityFeature = sys.modules[
        "homeassistant.components.climate"
    ].ClimateEntityFeature

    LIGHT_BASE = [
        "TurnOn", "TurnOff", "TurnOnOff", "SetBrightness",
        "SetColorTemperature", "SetBrightnessColorTemperature",
    ]

    devices = {
        # 主卧灯带：RGB + 色温双模式
        2907: device(
            2907, "主卧灯带", "Dimmer", LIGHT_BASE + ["SetColor"],
            {
                "turnOnOff": True, "brightness": 100, "colorTemperature": 76,
                "color": {"Red": 255, "Green": 251, "Blue": 255}, "dynamicId": 0,
            },
            model="6023",
        ),
        # 客厅灯带：云端谎报 SetColor（实际无 RGB 灯珠），靠 CAPABILITY_OVERRIDES 校正
        2905: device(
            2905, "客厅灯带", "Dimmer", LIGHT_BASE + ["SetColor"],
            {"turnOnOff": True, "brightness": 80, "colorTemperature": 100},
            model="6023",
        ),
        # 普通双色温灯
        2922: device(
            2922, "书桌灯", "Dimmer", LIGHT_BASE,
            {"turnOnOff": True, "brightness": 100, "colorTemperature": 100},
            model="6022",
        ),
        # 纯开关灯
        2899: device(
            2899, "主灯", "Light", ["TurnOn", "TurnOff", "TurnOnOff"],
            {"turnOnOff": True}, model="3129",
        ),
        # mix schema 空调（挂在 MultiInOnePanel 2998 下）
        2999: device(
            2999, "空调(mix)", "AirCondition",
            ["TurnOn", "TurnOff", "SetTemperature", "SetWindSpeed", "SetMode",
             "SetSwing", "TurnOnOff"],
            {"turnOnOff": True, "workMode": 1, "temperature": 24.0,
             "windSpeed": 4, "currentTemperature": 26.5},
            model="virtual_AC_3in1_mix", parent_id=2998,
        ),
        # 虚拟子设备空调（挂在 MultiInOneManager 2879 下，状态是字符串）
        2881: device(
            2881, "空调(virtual)", "AirCondition",
            ["TurnOn", "TurnOff", "SetTemperature", "SetWindSpeed", "SetMode",
             "TurnOnOff"],
            {"on": True, "mode": "COLD", "setTemp": 25, "curTemp": 29,
             "speed": "HIGH", "nodeId": "2"},
            model="virtual_AC_3in1", parent_id=2879, node_id="2",
        ),
    }
    # 虚拟子设备的父设备（eps[] 里镜像子设备状态）
    parent_2879 = device(
        2879, "暖通多合一", "MultiInOneManager",
        ["TurnOn", "TurnOff"], {}, model="3210",
    )
    parent_2879["cache"]["extension"] = {
        "EpTotal": 4,
        "eps": [
            {"mode": "COLD", "setTemp": 25, "epNum": 2, "online": True,
             "curTemp": 28, "speed": "HIGH", "on": True},
        ],
    }
    devices[2879] = parent_2879

    coord = FakeCoordinator(devices)

    # ---------------- 灯 ----------------
    section("灯：能力声明")
    e2907 = light.ZhisuanLightEntity(coord, 2907)
    e2905 = light.ZhisuanLightEntity(coord, 2905)
    e2922 = light.ZhisuanLightEntity(coord, 2922)
    e2899 = light.ZhisuanLightEntity(coord, 2899)

    check("双模式灯声明 {color_temp, rgb}",
          e2907._attr_supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.RGB},
          str(e2907._attr_supported_color_modes))
    check("双模式灯默认为 color_temp",
          e2907.color_mode == ColorMode.COLOR_TEMP, str(e2907.color_mode))
    check("双模式灯色温区间 2700-6500K",
          (e2907._attr_min_color_temp_kelvin, e2907._attr_max_color_temp_kelvin)
          == (2700, 6500))
    check("谎报 SetColor 的灯被校正为仅 color_temp",
          e2905._attr_supported_color_modes == {ColorMode.COLOR_TEMP},
          str(e2905._attr_supported_color_modes))
    check("普通双色温灯仅 color_temp",
          e2922._attr_supported_color_modes == {ColorMode.COLOR_TEMP})
    check("纯开关灯为 onoff",
          e2899._attr_supported_color_modes == {ColorMode.ONOFF})

    section("灯：color_mode 合法性（HA 会校验必须在 supported 里）")
    for label, ent in (("2907", e2907), ("2905", e2905), ("2922", e2922), ("2899", e2899)):
        check(f"{label} color_mode ∈ supported",
              ent.color_mode in ent._attr_supported_color_modes,
              f"mode={ent.color_mode}")

    section("灯：控制分发")

    async def light_controls():
        coord.calls.clear()
        await e2907.async_turn_on(color_temp_kelvin=2700)
        call = coord.calls[-1]
        check("调色温 → SetColorTemperature", call["action"] == "SetColorTemperature",
              call["action"])
        check("  字段是 value（不是 colorTemperature）",
              call["extension"] == {"value": 0}, str(call["extension"]))
        check("  切到 color_temp 模式", e2907.color_mode == ColorMode.COLOR_TEMP)

        coord.calls.clear()
        await e2907.async_turn_on(rgb_color=(255, 0, 0))
        call = coord.calls[-1]
        check("调颜色 → SetColor", call["action"] == "SetColor", call["action"])
        check("  字段是小写 red/green/blue（大写会返回 421）",
              call["extension"] == {"red": 255, "green": 0, "blue": 0},
              str(call["extension"]))
        check("  切到 rgb 模式", e2907.color_mode == ColorMode.RGB)

        coord.calls.clear()
        await e2907.async_turn_on(brightness=128, color_temp_kelvin=6500)
        call = coord.calls[-1]
        check("亮度+色温 → SetBrightnessColorTemperature",
              call["action"] == "SetBrightnessColorTemperature", call["action"])
        check("  colorTemperature=100（6500K 最冷）",
              call["extension"].get("colorTemperature") == 100, str(call["extension"]))

        coord.calls.clear()
        await e2907.async_turn_on(brightness=255)
        check("仅亮度 → SetBrightness", coord.calls[-1]["action"] == "SetBrightness")
        check("  不改变当前模式", e2907.color_mode == ColorMode.COLOR_TEMP)

        coord.calls.clear()
        await e2907.async_turn_on()
        check("无参数 → TurnOn", coord.calls[-1]["action"] == "TurnOn")

    asyncio.run(light_controls())

    section("灯：色温换算方向（0=暖 100=冷，与常见智能灯相反）")
    check("2700K → pct 0", light._kelvin_to_pct(2700) == 0)
    check("6500K → pct 100", light._kelvin_to_pct(6500) == 100)
    check("pct 0 → 2700K", light._pct_to_kelvin(0) == 2700)
    check("pct 100 → 6500K", light._pct_to_kelvin(100) == 6500)
    check("2000K 低于范围 → 钳到 0", light._kelvin_to_pct(2000) == 0)
    check("8000K 高于范围 → 钳到 100", light._kelvin_to_pct(8000) == 100)

    # ---------------- 空调 ----------------
    section("空调：能力声明")
    c2999 = climate.ZhisuanClimateEntity(coord, 2999)
    c2881 = climate.ZhisuanClimateEntity(coord, 2881)
    check("mix 空调有 FAN_MODE",
          bool(c2999.supported_features & ClimateEntityFeature.FAN_MODE))
    check("mix 空调有 TARGET_TEMPERATURE",
          bool(c2999.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE))
    check("有 SetSwing 的设备才有 SWING_MODE",
          bool(c2999.supported_features & ClimateEntityFeature.SWING_MODE))
    check("虚拟子设备空调无 SetSwing → 无 SWING_MODE",
          not (c2881.supported_features & ClimateEntityFeature.SWING_MODE))

    section("空调：状态读取（两种 schema）")
    check("mix 空调 hvac_mode 从 workMode 读",
          c2999.hvac_mode == HVACMode.HEAT, str(c2999.hvac_mode))
    check("mix 空调 fan_mode 从 windSpeed 读",
          c2999.fan_mode == "high", str(c2999.fan_mode))
    check("虚拟子设备 hvac_mode 从字符串 mode 读",
          c2881.hvac_mode == HVACMode.COOL, str(c2881.hvac_mode))
    check("虚拟子设备 fan_mode 从字符串 speed 读",
          c2881.fan_mode == "high", str(c2881.fan_mode))
    check("虚拟子设备 target_temperature 读 setTemp",
          c2881.target_temperature == 25, str(c2881.target_temperature))

    # 子设备自身 extension 缺字段时，回落到父设备 eps[]
    devices[2881]["cache"]["extension"] = {"nodeId": "2"}
    c2881_fallback = climate.ZhisuanClimateEntity(coord, 2881)
    check("子设备 ext 空时回落父设备 eps[].setTemp",
          c2881_fallback.target_temperature == 25,
          str(c2881_fallback.target_temperature))
    check("子设备 ext 空时回落父设备 eps[].mode",
          c2881_fallback.hvac_mode == HVACMode.COOL, str(c2881_fallback.hvac_mode))
    devices[2881]["cache"]["extension"] = {
        "on": True, "mode": "COLD", "setTemp": 25, "curTemp": 29,
        "speed": "HIGH", "nodeId": "2",
    }

    section("空调：模式数值映射（实测与官方文档相反）")
    check("COOL → mode 2（文档写 1）",
          climate.DEFAULT_MODE_MAP[HVACMode.COOL] == 2)
    check("HEAT → mode 1（文档写 2）",
          climate.DEFAULT_MODE_MAP[HVACMode.HEAT] == 1)
    check("除湿字符串 DEHUMIDIFICATION 能识别",
          climate.VIRTUAL_MODE_REVERSE.get("DEHUMIDIFICATION") == HVACMode.DRY)

    section("空调：控制分发")

    async def climate_controls():
        coord.calls.clear()
        await c2999.async_set_hvac_mode(HVACMode.COOL)
        call = coord.calls[-1]
        check("mix 调制冷 → SetMode mode=2",
              (call["action"], call["extension"]) == ("SetMode", {"mode": 2}),
              f"{call['action']} {call['extension']}")

        coord.calls.clear()
        await c2881.async_set_hvac_mode(HVACMode.HEAT)
        call = coord.calls[-1]
        check("虚拟子设备调制热 → SetMode mode=1",
              (call["action"], call["extension"]) == ("SetMode", {"mode": 1}),
              f"{call['action']} {call['extension']}")

        coord.calls.clear()
        await c2999.async_set_fan_mode("medium")
        call = coord.calls[-1]
        check("mix 调风速 → SetWindSpeed value=3（数字）",
              (call["action"], call["extension"]) == ("SetWindSpeed", {"value": 3}),
              f"{call['action']} {call['extension']}")

        coord.calls.clear()
        await c2881.async_set_fan_mode("medium")
        call = coord.calls[-1]
        check("虚拟子设备调风速 → SetWindSpeed speed='MID'（字符串）",
              (call["action"], call["extension"]) == ("SetWindSpeed", {"speed": "MID"}),
              f"{call['action']} {call['extension']}")

        coord.calls.clear()
        await c2999.async_set_temperature(temperature=22)
        call = coord.calls[-1]
        check("调温度 → SetTemperature value=22.0",
              (call["action"], call["extension"]) == ("SetTemperature", {"value": 22.0}),
              f"{call['action']} {call['extension']}")

        coord.calls.clear()
        await c2999.async_set_hvac_mode(HVACMode.OFF)
        check("关机 → TurnOff", coord.calls[-1]["action"] == "TurnOff")

    asyncio.run(climate_controls())

    # ---------------- 场景 ----------------
    section("场景：命名与唯一性")
    scenes = {
        160348: {
            "sceneId": 160348, "sceneName": "离家模式", "roomName": "客厅",
            "sceneType": "Normal", "manual": True,
            "actionList": [{"actionType": "RoomDeviceLight", "actionName": "TurnOffAll"}],
        },
        160351: {
            "sceneId": 160351, "sceneName": "中性光", "roomName": "厨房",
            "sceneType": "Normal", "manual": True, "actionList": [],
        },
        160349: {
            "sceneId": 160349, "sceneName": "中性光", "roomName": "卫生间",
            "sceneType": "Normal", "manual": True, "actionList": [],
        },
        160521: {
            "sceneId": 160521, "sceneName": "灯光多控", "roomName": "客厅",
            "sceneType": "MultiControl", "manual": False, "actionList": [],
        },
    }
    scene_coord = FakeCoordinator(devices, scenes)
    s_away = pkg.scene.ZhisuanSceneEntity(scene_coord, scenes[160348])
    s_kitchen = pkg.scene.ZhisuanSceneEntity(scene_coord, scenes[160351])

    check("场景名带房间后缀", s_away.name == "离家模式（客厅）", s_away.name)
    check("同名场景靠房间区分", s_kitchen.name == "中性光（厨房）", s_kitchen.name)
    check("无房间名时只用场景名",
          pkg.scene.build_scene_name({"sceneName": "X", "sceneId": 1}) == "X")
    check("无场景名时回退为「场景 <id>」",
          pkg.scene.build_scene_name({"sceneId": 160355}) == "场景 160355")
    check("unique_id 格式为 scene_<sceneId>",
          s_away.unique_id == "scene_160348", str(s_away.unique_id))
    check("图标是 mdi（云端 icon 名 HA 不认）",
          s_away.icon == "mdi:playlist-play", str(s_away.icon))

    section("场景：属性")
    attrs = s_away.extra_state_attributes
    check("属性含 scene_id", attrs["scene_id"] == 160348, str(attrs))
    check("属性含房间", attrs["room"] == "客厅", str(attrs))
    check("属性含动作条数", attrs["action_count"] == 1, str(attrs))
    check("属性含 scene_type", attrs["scene_type"] == "Normal", str(attrs))

    section("场景：激活")

    async def scene_controls():
        scene_coord.scene_calls.clear()
        await s_away.async_activate()
        check("激活 → coordinator.async_activate_scene(sceneId)",
              scene_coord.scene_calls == [160348], str(scene_coord.scene_calls))

    asyncio.run(scene_controls())

    class BoomCoordinator(FakeCoordinator):
        async def async_activate_scene(self, scene_id):
            raise RuntimeError("cloud 500")

    HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError
    boom = BoomCoordinator(devices, scenes)
    s_boom = pkg.scene.ZhisuanSceneEntity(boom, scenes[160348])

    async def scene_error():
        try:
            await s_boom.async_activate()
        except HomeAssistantError as err:
            check("执行失败抛 HomeAssistantError（不会静默失败）", True, str(err)[:50])
        else:
            check("执行失败抛 HomeAssistantError（不会静默失败）", False, "没有抛异常")

    asyncio.run(scene_error())

    section("场景：只保留 manual=true（多控/定时场景不暴露）")

    class FakeApi:
        def __init__(self, scene_list):
            self.scene_list = list(scene_list)
            self.get_calls = []

        async def async_get_scenes(self, home_id, manual=None):
            self.get_calls.append((home_id, manual))
            return self.scene_list

    class FakeEntry:
        entry_id = "test_entry"

        def __init__(self, home_id=185):
            self.data = {"home_id": home_id}

    fake_api = FakeApi(list(scenes.values()))
    coord2 = pkg.coordinator.ZhisuanCoordinator(
        hass=None, entry=FakeEntry(185), api=fake_api
    )

    # 真实顺序：coordinator 首次刷新发生在平台 setup 之前，
    # 那时 sink 还没注册，实体由 scene 平台自己从缓存里建。
    landed = []
    asyncio.run(coord2.async_refresh_scenes())
    check("首次刷新（sink 未注册）不重复下发实体", landed == [], str(landed))
    check("缓存里只剩 manual=true 的 3 个场景",
          set(coord2.scenes) == {160348, 160351, 160349},
          str(sorted(coord2.scenes)))
    check("请求时带 manual=True 参数",
          fake_api.get_calls and fake_api.get_calls[0] == (185, True),
          str(fake_api.get_calls))

    # 平台 setup 注册 sink 之后
    coord2.set_scene_sink(lambda s: landed.extend(s))
    asyncio.run(coord2.async_refresh_scenes())
    check("已缓存的场景不会被重复下发", landed == [], str(landed))

    new_scene = {
        "sceneId": 160355, "sceneName": "客厅灯开", "roomName": "客厅",
        "sceneType": "Normal", "manual": True, "actionList": [],
    }
    fake_api.scene_list.append(new_scene)
    asyncio.run(coord2.async_refresh_scenes())
    check("APP 里新建的场景会自动下发成新实体",
          [s["sceneId"] for s in landed] == [160355], str(landed))
    check("新场景进入缓存", 160355 in coord2.scenes)
    check("多控场景（manual=false）始终不进来", 160521 not in coord2.scenes)

    section("场景：布尔字段解析（云端有时回字符串）")
    check("as_bool('true') → True", pkg.api.as_bool("true") is True)
    check("as_bool(True) → True", pkg.api.as_bool(True) is True)
    check("as_bool('false') → False", pkg.api.as_bool("false") is False)
    check("as_bool('yes') → True", pkg.api.as_bool("yes") is True)
    check("as_bool(None) → None", pkg.api.as_bool(None) is None)
    check("as_bool('乱码') → None", pkg.api.as_bool("乱码") is None)


def main() -> int:
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="显示通过的断言")
    args = ap.parse_args()
    VERBOSE = args.verbose

    run()

    print("\n" + "=" * 56)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for item in FAILS:
            print(f"   - {item}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
