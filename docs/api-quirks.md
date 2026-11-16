# 挚算开放平台 API 实测笔记

> 这份文档是整个项目**最值钱的部分**。官方 PDF（`挚算科技开放平台API#中文.pdf`，v3.0.5）
> 有大量错误和缺失，下面每一条都是拿真实账号、真实设备打出来的。
> **改代码前先读这里，别信文档。**

服务端：
- 生产 `https://app.aioteco.com/openApi`（用户实际用的）
- 开发 `https://apptest.aioteco.com/openApi`（测试环境经常整站不可用，登录直接 JSONDecodeError）

---

## 1. 认证（OAuth 2.0）

```
POST /oauth2/code     (x-www-form-urlencoded)
  username, userPassword, client_id, regionId=CN, response_type=code, countryCode=86
POST /oauth2/token    (x-www-form-urlencoded)
  client_id, client_secret, code, grant_type=authorization_code, redirect_uri=http://
```

**必须踩过的三个坑：**

| 坑 | 现象 | 修复 |
|---|---|---|
| 少了 `countryCode` | HTTP 404 | 传 `"86"`（中国大陆） |
| `regionId` 传空串或 `LOCAL` | HTTP 500 | 固定传 `"CN"` |
| `redirect_uri` 传真实地址 | 换 token 失败 | 随便传 `http://` 即可，服务端不校验 |

- `access_token` 有效期 **90 天**（`expires_in: 7776000`）
- 刷新走同一个 `/oauth2/token`，`grant_type=refresh_token`
- 无 refresh_token 时只能重新走账号密码登录

**401 自动恢复**（重要）：业务接口返回的是 HTTP 200 + body `{"code": 401, ...}`，
不是 HTTP 401。所以 `_parse_response()` 遇到 `code==401` 必须抛 `ZhisuanAuthError`
（**不能抛 `ZhisuanApiError`**），否则外层 `except ZhisuanAuthError` 的自动重登分支永远进不去。
v1.3.1 之前这个 bug 让"token 过期后所有控制失效"持续了很久。

---

## 2. 请求头

每个业务接口都要带：

```
clientId:    <client_id>
authorization: <access_token>      ← 直接放 token，不加 "Bearer "
homeId:      <home_id>             ← 家庭级接口必带
language:    zh
version:     1.0
content-type: application/json
```

**`homeId` 是 HTTP header，不是 query 参数。** 放 query 里会 HTTP 500。
`GET /v1/home` 例外（不需要 homeId）。

---

## 3. 响应信封与结构

```json
{ "code": 200, "info": "SUCCESS", "data": { ... }, "messageId": "..." }
```

各接口的 `data` 形状（**和文档写的不一样，以实测为准**）：

| 接口 | `data` 形状 |
|---|---|
| `GET /v1/home` | `{"homeList": [{"homeId", "homeName", "areaList": [{"roomList": [...]}]}]}` |
| `GET /v1/room` | `{"roomList": [{"roomId", "roomName"}]}` |
| `GET /v1/device` | `{"pagePojo": {"page", "pageSize", "pageTotal", "dataTotal", "list": [Device]}}` |

**翻页注意**：翻到最后一页之后再请求，偶尔返回 `data: null`。
当成"没有更多"处理，不要当错误抛。

**已知坏掉的接口**：`GET /v1/device/{userDeviceId}`（按 ID 取单台）
会返回 HTTP 500 或 `{"error": "Internal Server Error"}`。
**别用它**，从设备列表里筛。`api.py` 里虽然留了 `async_get_device()`，但实际代码都走列表。

---

## 4. 设备控制：每个属性是独立 action

```
POST /v1/device/control
{ "userDeviceId": 2907, "name": "<ActionName>", "extension": { ... } }
```

**核心认知：不是"TurnOn + extension 带亮度"，而是"SetBrightness 这个独立 action"。**
早期版本把 brightness 塞进 TurnOn 的 extension，云端直接丢弃 → 表现为"只能开关"。

### 4.1 extension 字段名（**全部与文档不符**）

| Action | extension 字段 | 说明 |
|---|---|---|
| `SetBrightness` | `{"brightness": 0-100}` | 和文档一致 |
| `SetColorTemperature` | `{"value": 0-100}` | **不是** `colorTemperature` |
| `SetBrightnessColorTemperature` | `{"brightness": 0-100, "colorTemperature": 0-100}` | 这个反而用 `colorTemperature`，注意区分 |
| `SetColor` | `{"red": 0-255, "green": 0-255, "blue": 0-255}` | **必须小写**。大写 `Red/Green/Blue` 会返回 `code=421 参数不能为空` |
| `SetTemperature` | `{"value": <float>}` | 不是 `temperature` |
| `SetWindSpeed` | mix schema: `{"value": 0/2/3/4}`<br>虚拟子设备: `{"speed": "HIGH"/"MID"/"LOW"}` | **两种 schema 字段名不一样**，见 §6 |
| `SetMode` | `{"mode": <int>}` | 两种 schema 都是数字 |
| `SetSwing` | `{"mode": 1/0}` | 1=开 0=关 |
| `SetPosition` | `{"position": 0-100}` | 窗帘 |
| `TurnOn` / `TurnOff` | 无 extension | |

### 4.2 状态字段 ≠ 控制字段

读状态用的字段名和控制写的字段名**不是一套**：

| 概念 | 状态读取（`cache.extension`） | 控制写入（`extension`） |
|---|---|---|
| 亮度 | `brightness` | `brightness` |
| 色温 | `colorTemperature` | `value` |
| 颜色 | `color: {Red, Green, Blue}` （**大写**） | `red/green/blue` （**小写**） |

### 4.3 色温语义（和常识相反）

`colorTemperature` 是 0-100 百分比，但方向是：

- `0` → **暖光 2700K**（橙黄）
- `100` → **冷光 6500K**（白蓝）

大多数智能灯是反的，这里要小心。设备实际区间取 2700–6500K。

### 4.4 空调模式数值（和文档相反）

文档 §7.5 写 `1=COLE(COOL) 2=HEAT 3=FAN`，**实测是反的**：

```python
HVACMode.HEAT      → mode 1   # 文档说 2
HVACMode.COOL      → mode 2   # 文档说 1
HVACMode.FAN_ONLY  → mode 3
HVACMode.DRY       → mode 4
HVACMode.AUTO      → mode 5
```

设备关着时直接发 `SetMode` 会被丢弃 → 先 `TurnOn`，`await asyncio.sleep(0.5)` 再 `SetMode`。

### 4.5 回读状态字段的字符串形态

- `mode`（虚拟子设备）是**字符串**：`"COLD"` / `"HOT"` / `"FAN"` / `"DRY"` / `"AUTO"` / `"WIND"`
  - 除湿实测返回 `"DEHUMIDIFICATION"`（不是 `"DRY"`），漏了会显示 unknown
- `speed`（虚拟子设备）是**字符串**：`"HIGH"` / `"MID"` / `"LOW"`
- `windSpeed`（mix schema）是**数字**：`0/2/3/4`
- 温度可能是数字也可能是字符串 `"24.0"`，一律 `float()` 兜底

---

## 5. 空开 / Plug 的功率

设备**硬件支持功率计量**（如 Konke 10A 插座），但：

- `cache.extension` 里**没有任何功率字段**（只有 `turnOnOff` + mac/hardwareV 等元数据）
- 主动 `Query` 拿全量 `deviceCache` 也**没有**功率
- **只有 `QueryDisconnector` 能拿到**

```
POST /v1/device/query
{ "userDeviceId": 2888, "name": "QueryDisconnector" }
→ { "data": { "deviceInfo": { "data": "48.0" } } }     ← 字符串形式的瓦数
```

`QueryStatisticsLog`（电量日志）对 Plug 返回 HTTP 500，不可用。

**云端不会推送功率变化**：`EMessageType` 只有
`Normal / DeviceAction / DeviceOnline / DeviceOffline / DeviceReport / Notification / HostUnbind / DoorBell / Alarm`，
没有功率事件；`DevicePropName` 表里也没有 power。所以**功率只能轮询**。

实测云端扛得住高频轮询：连续 30 次 `QueryDisconnector` 平均 318ms、0 失败、无限流。
当前实现是独立的 5 秒后台轮询任务（`coordinator.async_start_plug_power_poll`），
和主 60 秒整轮解耦。

---

## 6. 空调的两种 schema（最容易踩的坑）

同样是 `type: "AirCondition"`，挂在不同的父设备下，**字段命名完全不同**：

| | mix schema | 虚拟子设备 schema |
|---|---|---|
| 父设备 | `MultiInOnePanel`（model 3211，id 2998） | `MultiInOneManager`（model 3210，id 2879） |
| 子设备 model | `virtual_AC_3in1_mix` | `virtual_AC_3in1` |
| 开关 | `turnOnOff` (bool) | `on` (bool) |
| 模式 | `workMode` (int) | `mode` (**字符串**) |
| 目标温度 | `temperature` | `setTemp` |
| 当前温度 | `currentTemperature` | `curTemp` |
| 风速 | `windSpeed` (int) | `speed` (**字符串**) |
| 写入风速 | `{"value": 3}` | `{"speed": "MID"}` |
| 状态来源 | 子设备自己的 `cache.extension` | 自己的 ext **或** 父设备 `extension.eps[]` |

**父设备 `eps[]` 结构**（`epNum` 对应子设备 `nodeId`）：

```json
"eps": [
  {"mode": "COLD", "setTemp": 25, "epNum": 2, "online": true,
   "curTemp": 28, "speed": "HIGH", "on": true, "onlineState": "ONLINE"},
  {"gwStatusCode": 0, "epNum": 1, "type": "KONKE_ZIGBEE_HVAC_ALL_IN_ONE_GATEWAY"}
]
```

**判定方式**：看 `device.parentId` 落在哪个集合，见 `climate.py` 的
`VIRTUAL_PARENT_IDS` / `MIX_PARENT_IDS`。这是**写死设备 ID** 的，
换一套房子就得重新探测（用 `tools/probe_devices.py`）。

**读取优先级**：子设备自己的 `extension` 优先（webhook 推的是这个，最实时），
为空时回落到父设备 `eps[]`。反过来会让状态回弹。

---

## 7. Webhook 推送

```
POST /v1/subscribe   { "homeId": "185", "notifyUri": "https://<公网地址>/api/zhisuan/webhook/<hook_id>" }
DELETE /v1/subscribe/{homeId}
```

- **公网地址**必须能被挚算云访问到。优先级：HA `external_url` → Cloudflared quick tunnel → Nabu Casa
  - Nabu Casa 的 `ui.nabu.casa` 在国内被墙，放最后
  - 用户实际用的是 ddnsto（`https://xxx.x.ddnsto.com/`）
  - Cloudflared quick tunnel 的 URL 每次重启都变 → `webhook.py` 每 6 小时比对一次、变了就重订阅
- HA 2026 里 `ha_webhook.async_register` 只接受函数 handler，不接受 view 对象 →
  用 `hass.http.register_view(view)`（跨版本安全）

### 推送字段白名单必须两种 schema 都覆盖

`webhook.py` 的 `DEVICE_REPORT_KEYS` 是个**白名单**，不在里面的字段会被丢掉。
历史 bug：只列了 mix schema 的字段名（`turnOnOff`/`temperature`/`windSpeed`），
虚拟子设备的 `setTemp`/`curTemp`/`speed`/`on` 被过滤掉 →
表现为"HA 里调到 25 度，卡片又回弹成 24"。

新增设备类型时**两套字段名都要加**。

---

## 8. 云端会撒谎 / 有 bug

1. **`actionList` 谎报能力**
   客厅灯带（2905，model 6023）声称支持 `SetColor`，实际没有 RGB 灯珠。
   不剔除的话 HA 会声明成彩色灯，任何调色命令被设备忽略 → 灯"打不开"。
   处理：`light.py` 的 `CAPABILITY_OVERRIDES` 按 `userDeviceId` 剔除。

2. **`keyValue` 复合键与单字段不一致**
   主卧空调 2881 的 `keyValue: "cool_24"`，但同一条记录里 `mode: "WIND"`、`setTemp: 25`。
   云端自己的内部状态不同步。只能靠 60 秒轮询兜底收敛。

3. **`SetWindSpeed {"speed": "AUTO"}` 无效**
   虚拟子设备不认 AUTO 字符串，状态不会变。`HIGH/MID/LOW` 都正常。
   → HA 里调"自动"档目前对虚拟子设备不生效，是已知限制。

4. **`color` 字段永不清空**
   从 RGB 切到色温模式后，`color` 仍保留旧值（如 `{Red:255,Green:251,Blue:255}`）。
   不影响控制，但意味着**不能用"color 是否存在"判断当前模式**。

---

## 9. Home Assistant 2026 破坏性变更（踩过的）

| 变更 | 现象 | 修复 |
|---|---|---|
| `ClimateEntityFeature.MODE` 被删 | `AttributeError` | 模式 UI 由 `_attr_hvac_modes` 长度驱动，不需要 feature flag |
| `UnitOfIlluminance` 被删 | ImportError | 用 `homeassistant.const.LIGHT_LUX` |
| `ha_webhook.async_register` 只收函数 | webhook 注册失败 | 用 `hass.http.register_view()` |
| 禁止从线程 `async_create_task` | `RuntimeError: hass.async_create_task from thread` | webhook 里直接在事件循环调，别丢 executor |

---

## 10. 调试方法论

**别在 HA 上试错。** 用户那边改一版要 HACS 更新 + 重启，一轮 15 分钟。

正确的顺序：

1. **用 `tools/probe_devices.py` 看真实能力** —— actionList 是什么、extension 里有哪些字段
2. **直接打云端验证行为** —— 发一条控制，等 2-3 秒，再读设备列表看状态变没变。
   这一步能区分"字段名错"（状态不变）和"代码逻辑错"（字段名对但没发出去）
3. **用 `tools/test_logic.py` 跑逻辑桩测试** —— 不启动 HA，验证能力声明/控制分发/数值映射
4. 三步都过了再推版本给用户

**排查"某字段名对不对"的技巧**：用不存在的 `userDeviceId`（如 999999）发控制命令。
- 返回 `code=404 该设备不存在` → **字段名是合法的**，云端解析通过了
- 返回 `code=421 xxx must be not empty` → **字段名错了**，云端找不到这个参数

---

## 11. 账号与凭据

- Client ID / Secret / 账号密码由用户提供，**绝不写进仓库、不写进 shell 历史**
- 测试脚本一律读环境变量（见 `tools/probe_devices.py` 顶部）
- 用完 `rm -f ~/.config/zs-openapi/session.json`（如果用官方 CLI）
