# AGENTS.md — 挚算智能家居 → Home Assistant 集成

> 给 Mavis（MiniMax Code）的项目级规范。本文件遵循 [agents.md](https://agents.md/) 规范。

## 1. 项目目标

把**挚算科技**（杭州挚算科技有限公司）的小众智能家居云接入 **Home Assistant**。用户情况：
- 智能硬件 PM，50+ 设备，5 类（智能开关/调光灯/窗帘/空调/传感器）
- 挚算无官方 HA 集成，走 OAuth 2.0 + REST + Webhook
- HA 装在本地 ARM 小主机（HA OS 完整版，有 Add-on 商店）
- 用户不会部署，部署流程必须"点几下"

**核心交付物**：`custom_components/zhisuan/` — 一个完整的 HA custom component。

## 2. 目录约定

```
zhisuan-ha/
├── AGENTS.md                      ← 本文件
├── README.md                      ← 部署文档（用户视角）
├── custom_components/
│   └── zhisuan/                   ← 集成代码（直接拷到 HA /config/custom_components/zhisuan/）
│       ├── manifest.json
│       ├── const.py
│       ├── api.py
│       ├── config_flow.py
│       ├── coordinator.py
│       ├── webhook.py
│       ├── __init__.py
│       ├── entity.py
│       ├── switch.py
│       ├── light.py
│       ├── cover.py
│       ├── climate.py
│       ├── sensor.py
│       ├── binary_sensor.py
│       └── translations/
│           └── zh-Hans.json
└── tools/
    └── selftest.py                ← OAuth + 拉设备 自测脚本
```

**绝对不要**在 `custom_components/zhisuan/` 之外的地方放代码。

## 3. 命名规范

- **HA domain**：`zhisuan`（全小写，HA 要求）
- **类名前缀**：`Zhisuan...`（如 `ZhisuanApi`, `ZhisuanLightEntity`）
- **私有变量**：下划线前缀（`_client`, `_home_id`）
- **常量**：`UPPER_SNAKE_CASE`（如 `DOMAIN`, `CONF_CLIENT_ID`）
- **HA 实体 ID**：`{domain}.{device_name}_{node_id or device_id}` — 用 `slugify`，中文名走 transliterate

## 4. 关键 API 速查（避免每次翻 PDF）

### 服务端
- 开发：`https://apptest.aioteco.com/openApi`
- 生产：`https://app.aioteco.com/openApi`

### OAuth 流程
1. `POST /v1/register` — 注册用户（首次）
2. `POST /oauth2/code` — 拿授权码
3. `POST /oauth2/token` — 换 access_token（`grant_type=authorization_code`）
4. `POST /oauth2/token` — 刷新 token（`grant_type=refresh_token`）
5. **access_token 有效期 90 天**（`expires_in: 7776000` 秒）

### 必传 HTTP Headers
- `clientId` / `client_id`：第三方平台标识
- `authorization`：Bearer access_token
- `homeId`：家庭 ID（0 表示全局）
- `language`：zh / en
- `version`：1.0
- `content-type`：application/json（body）/ application/x-www-form-urlencoded（OAuth）

### 核心 REST 接口
- `GET /v1/home` — 家庭列表
- `GET /v1/room?homeId=X` — 房间列表
- `GET /v1/device?homeId=X&page=1&pageSize=50` — 设备列表（分页）
- `GET /v1/device/{userDeviceId}` — 单个设备
- `POST /v1/device/control` — 控制设备（通用）
- `POST /v1/subscribe` — **订阅家庭通知**（Webhook）
- `DELETE /v1/subscribe/{homeId}` — 取消订阅

### 设备控制（POST /v1/device/control）
Body 格式：
```json
{
  "userDeviceId": 5988,
  "name": "TurnOn" | "TurnOff" | "SetBrightness" | "SetPosition" | "SetMode" | "SetColorTemperature" | "SetColor" | "SetTemperature" | "SetWindSpeed" | "Pause" | "SetReverse" | ...,
  "extension": { ... }  // 各 action 需要的额外参数
}
```

### Webhook 通知（挚算云推过来）
POST 到我们订阅的 URL，body 是 Notify Object：
- `messageType`: `Normal` / `DeviceAction` / `DeviceOnline` / `DeviceOffline` / `DeviceReport` / `Notification` / `HostUnbind` / `DoorBell` / `Alarm`
- `name`: 子类型
- `data`: 不同类型不同结构
  - `DeviceReport` → `data.extension` 是设备当前状态（`turnOnOff`/`brightness`/`temperature`/...）+ `userDeviceId`
  - `DeviceOnline` / `DeviceOffline` → `data.userDeviceId`
  - `DeviceAction` → `data.userDeviceId` + `data.name`（谁在控制）

## 5. 设备 type → HA 实体映射

| 挚算 EDeviceType | HA 实体 | 关键 extension 字段 | 关键 action |
|---|---|---|---|
| `Switch`, `Plug` | `switch` | `turnOnOff` | `TurnOn`/`TurnOff` |
| `Light` (开关型) | `light` | `turnOnOff` | `TurnOn`/`TurnOff` |
| `Light` (调光) | `light` + brightness | + `brightness` (0-100) | + `SetBrightness` |
| `Light` (调色温) | `light` + color_temp | + `colorTemperature` | + `SetColorTemperature` |
| `Light` (调色) | `light` + color (RGB) | + `color` (object) | + `SetColor` |
| `Dimmer` | `light` | 同上 | 同上 |
| `Curtains`, `CurtainsMotor` | `cover` | `operationMode` (0关/1开/2停), `position` (0-100) | `SetPosition`, `SetReverse` |
| `AirCondition`, `AirConditionManager` | `climate` | `turnOnOff` + `mode` + `temperature` + `windSpeed` | `TurnOn`/`TurnOff`/`SetMode`/`SetTemperature`/`SetWindSpeed` |
| `FloorHeating` | `climate` | 同上 | 同上 |
| `Sensor`, `Detector` (多属性) | 多个 `sensor` / `binary_sensor` | `temperature`/`humidity`/`battery`/`illuminance`/`PM25`/`co2`/`motionAlarmState`/`contactState`/`waterSensorState`/`smokeSensorState`/`gasSensorState`/... | 只读 |
| `Infrared` (空调伴侣) | `climate` | 透过 `SendData` 控制 | `SendData` |

**多路设备**（`isVirtual=true`）：每个 `nodeId` 单独建一个 HA 实体，命名 `原名_1`、`原名_2`。

**决策逻辑**：实体的能力由设备的 `actionList` 决定，不要"全开"。例如 `Light` 设备 `actionList` 里有 `SetBrightness` 才有 brightness。

## 6. 关键约束

- **token 90 天** → 必须做自动 refresh，refresh 失败要触发 reauth
- **webhook 订阅** → 集成 setup 时调一次，**集成 unload 时 DELETE 取消**
- **cloudflared URL 不稳定**（trycloudflare 模式）→ 集成启动时检查订阅的 URL 与当前 URL，不一致就重新 POST /v1/subscribe
- **ARM 兼容** → 纯 Python + aiohttp，零 C 扩展
- **密钥/凭证** → 不进代码、不进 commit；用户填到 HA config entry，HA 自动加密存
- **分页** → 设备列表有 `pageTotal`，循环拉全
- **HA 实体 unique_id** → `{userDeviceId}_{nodeId}` 或 `{userDeviceId}_{prop_name}`（Sensor 拆属性用）

## 7. 部署流程（用户视角）

详见 `README.md`。摘要：
1. HA 装 **Samba Add-on**（代码传输通道）
2. HA 装 **Cloudflared Add-on**（Webhook 公网通道）
3. mac Finder 连 `smb://<HA-IP>`，把 `custom_components/zhisuan/` 整个文件夹拖到 HA `/config/custom_components/`
4. HA 重启
5. HA Settings → Devices & Services → Add Integration → 搜 "zhisuan" → 填 clientId/secret/账号/密码
6. 集成自动订阅 Webhook，完事

## 8. 跑测命令

```bash
# 自测：OAuth + 拉设备（需要真实凭证）
cd /Users/dcc/.minimax/workspace/zhisuan-ha
python3 tools/selftest.py \
  --client-id <client_id> \
  --client-secret <client_secret> \
  --username <email> \
  --password <pwd> \
  --home-id <home_id>
```

自测脚本必须能：
- 走通 OAuth 拿到 token
- 调 `GET /v1/device?homeId=X&pageSize=50` 拉设备列表
- 打印每个设备的 type/actionList/extension 关键字段
- 验证控制（`TurnOn` / `TurnOff`）能成功（但不要真去关灯，让用户决定）

## 9. 已知坑 / 待验证

1. **空调 mode 数值因设备而异** — 文档明确写了 mode 1/2/3 对 COOL/HEAT/FAN 还是别的，要看具体设备。
2. **多路设备 nodeId 范围** — 文档说 1-100，实际可能 1-N。
3. **Webhook URL 重新订阅的频率** — 集成启动时 + 每 6 小时兜底一次。
4. **color 字段格式** — 文档说 `color: Object`，具体是 `{r, g, b}` 还是 `{hue, saturation}` 待验证。
5. **红外空调伴侣** — 状态怎么读（`SendData` 是控制，状态可能要走其他字段）待验证。

## 10. 不要做的事

- 不要碰用户的 `.env` / token / 密钥
- 不要 `git push` / `npm publish` / 部署到生产（除非显式授权）
- 不要给挚算云发大量请求做"压测"
- 不要在没有真机的情况下自信地说"一定 work"——每个写完的函数都要在自测脚本里跑过
- 不要注释掉报错或加 try/except 绕过
