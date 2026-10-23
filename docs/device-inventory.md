# 用户设备实测清单

> 快照时间：2026-08（`homeId=185`，共 **66 台**）。
> 换一套房子/加设备后，用 `tools/probe_devices.py` 重新生成即可。
> 这张表的价值在于：**标出了哪些设备是特殊的**，正常设备不用管。

## 设备树

```
Host 2865 (ccu_28)
├── Gateway 2866 (gw1)
│   └── Moore 2867 (Moore 4s 中控屏)
│       ├── 2868 灯1   ┐ model 3022，纯开关
│       └── 2869 灯2   ┘
└── Gateway 2871 (gw0)   ← 绝大多数设备挂这里
    ├── Dimmer ×21          灯（6022/6023/3137）
    ├── Light ×13           灯（3022/3069/3129/3151/6004/3130）
    ├── 多路面板
    │   ├── 2894 双路灯控模块(3069) → 2895 吧台灯 / 2896 条件灯
    │   ├── 2901 三路灯控(3151)    → 2902 主灯 / 2903 射灯 / 2904 灯带
    │   ├── 2918 双路灯控(3130)    → 2919 镜前灯 / 2920 主卫灯开关
    │   ├── 2911 四路自定义面板(MixLightTouchPanel 3203) → 2912~2915 virtual_light
    │   └── 2923 三路自定义面板(MixLightTouchPanel 6026) → 2924~2926 virtual_light
    ├── SceneTrigger ×4    四路快捷面板(6014) → HA switch
    ├── CurtainsMotor ×2   2890 窗纱 / 2892 窗帘 (3068)
    ├── Sensor ×2          2878 人体(3053) / 2883 温湿度(3052)
    ├── Plug ×1            2888 投影仪 (3037)
    ├── AirFresher ×1 → 实为 2998 下的虚拟子设备（见下）
    ├── MultiInOneManager 2879 (暖通多合一 3210)
    │   └── 2880 / 2881 / 2882 空调 (virtual_AC_3in1)   ← 虚拟子设备 schema
    └── MultiInOnePanel 2998 (地暖多合一 3211)
        ├── 2999 空调 (virtual_AC_3in1_mix#2)          ← mix schema
        ├── 3000 地暖 (virtual_FH_3in1_mix)
        └── 3001 新风 (virtual_AF_3in1_mix)
```

## 需要特别注意的设备

| id | 名称 | model | 为什么特殊 | 代码里对应的处理 |
|---|---|---|---|---|
| **2907 / 2891** | 主卧灯带 | 6023 | 真·双模式：`SetColor` + `SetColorTemperature` 都有 | `light.py` 声明 `{COLOR_TEMP, RGB}` + 动态 `color_mode` |
| **2905** | 灯带 | 6023 | **能力谎报**：actionList 说有 `SetColor`，实际无 RGB 灯珠 | `light.py` 的 `CAPABILITY_OVERRIDES[2905]` |
| **2880/2881/2882** | 空调 | virtual_AC_3in1 | 虚拟子设备 schema：`on`/`mode`(字符串)/`setTemp`/`curTemp`/`speed`(字符串) | `climate.py` 的 `VIRTUAL_PARENT_IDS = {2879}` |
| **2999** | 空调 | virtual_AC_3in1_mix#2 | mix schema：`turnOnOff`/`workMode`/`temperature`/`windSpeed`(数字) | `climate.py` 的 `MIX_PARENT_IDS = {2998}` |
| **2888** | 投影仪 | 3037 | 功率只能靠 `QueryDisconnector` 轮询，云端不推送 | `coordinator.async_start_plug_power_poll()`（5s） |
| **2879** | 暖通多合一 | 3210 | 父设备，`extension.eps[]` 里镜像 3 台子空调的状态 | `_virtual_state()` 回落到这里 |
| **2865 / 2866 / 2871** | Host / Gateway | — | 基础设施，HA 里不建实体 | 不在 `DEVICE_TYPE_TO_HA_PLATFORM` 里 |

## 按类型统计

| 类型 | 数量 | HA 实体 |
|---|---|---|
| Light | 22 | `light`（其中 13 台是纯开关灯，只有 on/off） |
| Dimmer | 21 | `light`（全部支持亮度+色温；2 台支持 RGB） |
| AirCondition | 4 | `climate`（3 虚拟 + 1 mix） |
| SceneTrigger | 4 | `switch` |
| MixLightTouchPanel | 2 | `switch`（其下 virtual_light 各自是 `light`） |
| CurtainsMotor | 2 | `cover` |
| Sensor | 2 | `sensor` / `binary_sensor`（按属性拆多个实体） |
| Gateway | 2 | 不建实体 |
| AirFresher | 1 | `switch`（暂用 switch，未来可改 fan） |
| FloorHeating | 1 | `climate` |
| MultiInOnePanel | 1 | `climate` |
| Plug | 1 | `switch` + `sensor`（实时功率） |
| MultiInOneManager | 1 | `climate` |
| Moore | 1 | 不建实体 |
| Host | 1 | 不建实体 |

## 纯开关灯清单（只有 on/off，没有亮度色温）

这些设备 `actionList` 只有 `TurnOn/TurnOff/TurnOnOff`，
HA 里 `supported_color_modes = {onoff}`，**不要指望有亮度滑块**：

```
2868 灯1  2869 灯2  2876 台面灯  2894 双路灯控模块  2899 主灯
2901 三路灯控  2910 主灯  2918 双路灯控
```
（多路面板的子灯 2895/2896/2902/2903/2904/2912~2915/2919/2920/2924~2926 也是纯开关）

## 传感器属性

- **2878 人体传感器** (3053)：`motionAlarmState`、`battery`、`illuminance`(?)
- **2883 温湿度传感器** (3052)：`temperature`、`humidity`、`battery`

`sensor.py` 只为 `extension` 里**实际存在**的属性建实体，没数据的属性不建
（历史 bug：给开关类设备硬建 battery sensor，把设备拖成 unavailable）。
