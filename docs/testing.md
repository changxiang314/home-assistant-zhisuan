# 测试与调试方法

## 铁律：别在 HA 上试错

用户的 HA 跑在家里的 ARM 小主机上，改一版要 **HACS 更新 → 重启 → 等 15 分钟**。
所以任何改动都要先在本地三步验证完，再推给用户。

```
① 探测真实能力   →  ② 直连云端验证行为  →  ③ 跑逻辑桩测试  →  ④ 推版本
 probe_devices.py      curl/python 脚本        test_logic.py
```

---

## ① `tools/probe_devices.py` —— 看设备真实能力

判断"某类设备在 HA 里表现不对"的第一工具。

```bash
export ZS_CLIENT_ID=...
export ZS_CLIENT_SECRET=...
export ZS_USERNAME=...
export ZS_PASSWORD=...
export ZS_HOME_ID=185

python3 tools/probe_devices.py                  # 全量概览 + 按类型汇总
python3 tools/probe_devices.py --type Dimmer    # 只看调光灯
python3 tools/probe_devices.py --id 2907        # 单台，打印完整 extension
python3 tools/probe_devices.py --json dump.json # 原始数据存档
```

它会自动标注：
- **双模式灯(RGB+色温)** —— 需要声明 `{color_temp, rgb}`
- **虚拟子设备(读父 eps[])** / **mix 子设备**
- **能力谎报(已校正剔除 …)** —— 对照 `light.py` 的 `CAPABILITY_OVERRIDES`
- **多路 nodeId=N**

**看什么**：`actionList`（决定实体能力）和 `cache.extension`（决定状态字段名）。
两者都可能和官方文档不符。

---

## ② 直连云端验证行为

改控制命令之前，先确认云端到底认哪个字段名。

### 技巧 A：用不存在的设备 ID 试探字段名

```python
# 只验证"云端能不能解析这个 extension"，不会真的动设备
{"userDeviceId": 999999, "name": "SetWindSpeed", "extension": {"speed": "MID"}}
```

- `code=404 该设备不存在` → **字段名合法**（云端解析通过了，只是设备不存在）
- `code=421 xxx must be not empty` → **字段名错了**（云端找不到这个参数）

### 技巧 B：发命令 → 等 2-3 秒 → 重读设备列表

```python
await post("/v1/device/control", {"userDeviceId": 2907,
                                  "name": "SetColorTemperature",
                                  "extension": {"value": 20}})
await asyncio.sleep(3)
devices = await get("/v1/device?page=1&pageSize=50")
# 看 extension.colorTemperature 有没有从 100 变成 20
```

这一步能区分两类问题：
- **状态没变** → 字段名/数值错了，或者设备不支持
- **状态变了但 HA 没反应** → 集成侧的问题（webhook 白名单、读取优先级、实体能力声明）

**测完记得恢复原值**，别把用户家的灯留在奇怪状态。

⚠️ 注意：`GET /v1/device?page=2` 偶尔返回 `data: null`，重读时做好判空。

---

## ③ `tools/test_logic.py` —— 逻辑桩测试（不用启动 HA）

原理：造一套最小的 `homeassistant.*` 假模块塞进 `sys.modules`，
然后把 `custom_components/zhisuan` 当包加载，**真实执行集成代码**（不是复制逻辑）。

```bash
python3 tools/test_logic.py      # 全部跑，只显示失败
python3 tools/test_logic.py -v   # 显示每条断言
```

退出码 0 = 全过，1 = 有失败，可直接挂 CI。

**当前覆盖 47 条断言**，分六组：

| 组 | 验证内容 |
|---|---|
| 灯：能力声明 | 双模式 `{color_temp, rgb}`、`CAPABILITY_OVERRIDES` 生效、单模式声明 |
| 灯：color_mode 合法性 | 返回值必须在 `supported_color_modes` 里（HA 会校验） |
| 灯：控制分发 | 调色温→`SetColorTemperature{value}`、调色→`SetColor{red,green,blue}`、亮度+色温→组合 action、仅亮度→`SetBrightness` |
| 灯：色温换算 | 0↔2700K、100↔6500K、越界钳制 |
| 空调：能力声明 | `FAN_MODE` / `TARGET_TEMPERATURE` / `SWING_MODE` 按 actionList 出现 |
| 空调：状态读取与控制 | 两种 schema 各自读对字段、子设备回落父 `eps[]`、`COOL→2/HEAT→1`、风速字段按 schema 分流 |

**加测试的方式**：在 `devices` 字典里加一台假设备（照抄真实设备的
`actionList` + `extension`），然后加 `check()` 断言。桩里的 `FakeCoordinator`
会记录每次 `async_control` 调用，断言 `coord.calls[-1]` 即可。

桩模块需要跟真实 HA 行为一致才能测准 —— 比如 `ClimateEntity.supported_features`
是从 `_attr_supported_features` 派生的 property。如果某个断言莫名失败，先确认是
集成代码的问题还是桩造得不像。

---

## ④ 推版本给用户

- HACS 靠 **git commit hash** 检测更新，`manifest.json` 的 `version` 只是给人看的
  （但两边保持一致，方便用户核对）
- 推完告诉用户：HACS 更新 → 重启 HA → 测什么
- 让用户把 `开发者工具 → 状态` 里的实体的 `supported_features` / `supported_color_modes`
  等属性贴过来 —— 这是定位"能力没生效"最快的证据

---

## 常见故障对照表

| 现象 | 先查什么 |
|---|---|
| 全部设备都控制不了，日志有 `code=401` | 401 自动重登逻辑（`api._parse_response` 必须抛 `ZhisuanAuthError`） |
| 某台设备状态"回弹"到旧值 | webhook 白名单是否漏了该 schema 的字段名（`DEVICE_REPORT_KEYS`） |
| 卡片上少一套控件（如没色温滑块） | 实体的 `supported_color_modes` / `supported_features` 属性 |
| 调了没反应，但云端状态变了 | 集成侧读取优先级（子设备 ext vs 父 eps[]） |
| 空调模式调了反向 | `DEFAULT_MODE_MAP` 数值（实测与文档相反） |
| 灯"打不开" | 是否被错误声明成 RGB 灯（`CAPABILITY_OVERRIDES`） |
| 功率 sensor 不更新 | Plug 功率轮询任务是否启动（每 5s 一次，独立于主轮询） |
| 状态延迟 60 秒以上 | webhook 订阅成功了吗（看日志 `Subscribed home …`）、公网 URL 通不通 |

## 日志

集成所有 logger 名是 `custom_components.zhisuan`。调细节时在 `configuration.yaml` 加：

```yaml
logger:
  default: warning
  logs:
    custom_components.zhisuan: debug
```

关键日志锚点：
- `ZhiSuan refresh OK: home_id=… devices=… rooms=…` —— 主轮询正常
- `Subscribed home … to …` —— webhook 订阅成功
- `Auth error on GET … re-login and retry` —— 401 自动重登触发
- `设备 xxx 能力校正：剔除 [...]` —— CAPABILITY_OVERRIDES 生效
- `Plug power poll started: every 5.0s` —— 功率轮询启动
