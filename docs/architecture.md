# 代码结构与数据流

## 模块职责

```
custom_components/zhisuan/
├── manifest.json        HA 集成元数据（domain/version/requirements）
├── const.py             所有常量：endpoint、device type、action 名、字段名
├── api.py               挚算云 HTTP 客户端（OAuth + REST + 401 自动重登）
├── config_flow.py       UI 配置流程（填 clientId/secret/账号/密码/环境）
├── coordinator.py       数据中枢：设备缓存 + 主轮询 + Plug 功率高频轮询
├── webhook.py           入站推送接收 + 公网 URL 解析 + 订阅维护
├── __init__.py          生命周期入口（setup / unload / 迁移）
├── entity.py            所有实体的基类（device_info / available / ext 快捷方式）
├── switch.py            开关、插座、快捷面板
├── light.py             灯（开/关、调光、调色温、RGB、双模式）
├── cover.py             窗帘、窗帘电机
├── climate.py           空调、地暖、多合一（两种 schema）
├── sensor.py            数值传感器 + Plug 实时功率
├── binary_sensor.py     门磁/人体/水浸/烟感/燃气/SOS
└── translations/zh-Hans.json
```

## 数据流

```
                    ┌───────────────────────────────┐
                    │      挚算云 (app.aioteco.com)   │
                    └───────────────────────────────┘
                       ↑ 控制 POST /v1/device/control
                       │ 拉取 GET  /v1/device (分页)
                       │ 查询 POST /v1/device/query (QueryDisconnector)
                       │ 订阅 POST /v1/subscribe
                       ↓ 推送 DeviceReport / Online / Offline
   ┌──────────────────────────────────────────────────────┐
   │ ZhisuanApi (api.py)                                   │
   │  - 持有 token，临期自动 refresh                        │
   │  - 收到 code=401 抛 ZhisuanAuthError → 自动重登 + 重试  │
   └──────────────────────────────────────────────────────┘
              ↑                                    ↑
              │                                    │
   ┌──────────────────────┐          ┌──────────────────────────┐
   │ ZhisuanCoordinator   │          │ ZhisuanWebhookView       │
   │  每 60s 拉全量        │←─────────│  接收云端推送，写入缓存    │
   │  (兜底，防漏推送)      │  apply   │  不直接改 HA 状态         │
   │  每 5s 拉 Plug 功率   │  _push   └──────────────────────────┘
   └──────────────────────┘
              │ data: {userDeviceId: Device}
              ↓
   ┌──────────────────────────────────────────────────────┐
   │ 实体层 (switch / light / cover / climate / sensor…)   │
   │  CoordinatorEntity → 订阅 coordinator，数据变了自动刷新 │
   │  状态读：self.ext (= cache.extension)                 │
   │  控制写：coordinator.async_control(uid, action, ext)  │
   └──────────────────────────────────────────────────────┘
```

**两条数据源**：
- **推送（主）** —— 云端 `DeviceReport` 打进来，`webhook._dispatch` 过滤白名单字段后
  调 `coordinator.async_apply_push()` 直接改内存缓存并触发 listener。延迟 1-2 秒。
- **轮询（兜底）** —— 每 60 秒拉全量设备列表。防止推送丢失（比如 cloudflared URL 变了）。

改推送逻辑时**两套 schema 的字段名都要覆盖**，见 `docs/api-quirks.md` §7。

## 实体约定

| 项 | 规则 |
|---|---|
| `unique_id` | `{userDeviceId}` + （多路设备再拼 `_{nodeId}`）<br>Sensor 按属性拆分：`{userDeviceId}_{prop}` |
| `device_info.identifiers` | `(DOMAIN, str(userDeviceId))` —— 同一台设备的所有实体归到一个 HA 设备下 |
| `via_device` | 有 `parentId` 时指向父设备，否则 HA 会把子设备当孤立设备 |
| `suggested_area` | 用 `roomId` 查 coordinator 里的房间名映射 |
| `has_entity_name` | 全部 `True`，实体名 = 设备名 + 属性名 |
| `available` | `self.device is not None`。**不用** `super().available`（会导致控制得动但显示不可用） |

`entity.py` 提供的快捷属性：
- `self.device` → 整台设备的原始 dict
- `self.ext` → `cache.extension`，拿不到返回 `{}`
- `self.is_online` → 三级查找（`cache.isOnline` → `cache.extension.isOnline` → 默认 True）

## 能力判定原则

**实体的能力完全由设备的 `actionList` 决定，不要"全开"。**

例：`Light` 的 `actionList` 里有 `SetBrightness` 才有 `brightness` 属性；
有 `SetColorTemperature` 才声明 `color_temp` 模式。

灯的模式声明优先级（`light.py`）：

```
两者都有  → {COLOR_TEMP, RGB}   ← 双模式，必须两个都声明，否则少一套控件
只有 RGB  → {RGB}
只有色温  → {COLOR_TEMP}
只有亮度  → {BRIGHTNESS}
都没有    → {ONOFF}
```

双模式设备必须额外实现动态 `color_mode` property（HA 会校验返回值 ∈ `supported_color_modes`），
并声明 `min/max_color_temp_kelvin`。

`CAPABILITY_OVERRIDES`：云端 `actionList` 谎报时按 `userDeviceId` 剔除。加新条目要**注释写清为什么**。

## 生命周期（`__init__.py`）

```
async_setup_entry
  ├─ 建 ZhisuanApi + OAuth 登录（失败 → ConfigEntryNotReady，HA 会自动重试）
  ├─ token 持久化到 config entry（重启后不必重新登录）
  ├─ 建 Coordinator + 首次刷新
  ├─ _migrate_stale_battery_sensors()   ← 历史遗留实体清理
  ├─ 启动 Plug 功率 5s 轮询任务
  ├─ 注册 webhook view（hass.http.register_view）
  ├─ POST /v1/subscribe（失败不阻塞 setup，后台循环会重试）
  ├─ 启动 6 小时重订阅循环
  └─ 转发到各平台（switch/light/cover/climate/sensor/binary_sensor）

async_unload_entry
  ├─ 停重订阅循环 + 取消任务
  ├─ DELETE /v1/subscribe
  ├─ 卸载各平台
  └─ await coordinator.async_stop_plug_power_poll()
```

## 加一种新设备类型，要动哪

1. `const.py`：加 `DEVICE_TYPE_XXX`，加进 `DEVICE_TYPE_TO_HA_PLATFORM`
2. **先探测真实能力**：`python3 tools/probe_devices.py --type XXX`
3. 看字段名：读状态字段 vs 控制字段可能不一样（见 `docs/api-quirks.md` §4.2）
4. 写平台文件（或在已有平台里加分支），能力由 `actionList` 驱动
5. `docs/api-quirks.md` §7 的 `DEVICE_REPORT_KEYS` 补上该类型的**状态字段名**（否则推送被过滤）
6. `tools/test_logic.py` 加桩测试
7. `__init__.py` 的 `PLATFORMS` 加对应平台（如果是新平台）
