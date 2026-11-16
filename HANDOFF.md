# 交接说明

> 这份文档给**接手这个项目的人或 AI 工具**。
> 目标：读完能独立接手，不需要原作者在场。

---

## 一句话现状

挚算智联（杭州挚算科技）→ Home Assistant 的自定义集成，**已上线可用**，
用户家里 66 台设备全部接入，日常在用。当前版本 **v1.3.8**。

## 交付物清单

| 位置 | 内容 |
|---|---|
| `custom_components/zhisuan/` | 集成源码（15 个文件，约 2900 行），这就是产品 |
| `docs/api-quirks.md` | **挚算云真实行为手册**，官方 PDF 有大量错误，以这份为准 |
| `docs/architecture.md` | 模块职责、数据流、加新设备要动哪 |
| `docs/testing.md` | 本地验证方法论（**别在 HA 上试错**） |
| `docs/device-inventory.md` | 用户 66 台设备实测清单 + 哪些是特殊的 |
| `tools/probe_devices.py` | 设备能力探测器（排障第一工具） |
| `tools/test_logic.py` | 逻辑桩测试，47 条断言，不启动 HA |
| `tools/selftest.py` | 早期自测脚本（功能已被 probe_devices 覆盖） |
| `AGENTS.md` | 给 AI Agent 的项目规范（**AI 工具先读这个**） |
| `README.md` | 面向用户的部署文档 |
| GitHub | https://github.com/changxiang314/home-assistant-zhisuan |

## 5 分钟上手

```bash
# 1. 拿代码
git clone https://github.com/changxiang314/home-assistant-zhisuan
cd home-assistant-zhisuan

# 2. 装依赖
pip install aiohttp

# 3. 跑逻辑测试（不联网、不需要 HA，47 条断言应该全过）
python3 tools/test_logic.py -v

# 4. 接真机（凭据问用户要，见下）
export ZS_CLIENT_ID=... ZS_CLIENT_SECRET=...
export ZS_USERNAME=... ZS_PASSWORD=...
export ZS_HOME_ID=185
python3 tools/probe_devices.py --type Dimmer
```

## 你需要从用户那里拿到什么

| 项 | 说明 |
|---|---|
| 挚算开放平台 **Client ID / Client Secret** | 挚算开放平台后台申请 |
| 挚算 **账号密码** | 用户手机号 `151****9404` 对应的账号 |
| **homeId** | 用户家是 `185` |
| **GitHub 推送权限** | 仓库 `changxiang314/home-assistant-zhisuan` 的 token（让用户生成 classic token，`repo` scope，用完即撤销） |
| **HA 访问入口** | 用户家里 HA 的 Web UI（用 ddnsto 内网穿透暴露） |

⚠️ **凭据一律走环境变量，不写进任何文件、不写进 shell 历史、不 commit。**
用户对这点很在意（他明确要求过"用完撤销"）。

## 用户环境

- HA OS 完整版（有 Add-on 商店），跑在 ARM 小主机
- 内网穿透：**ddnsto**（`https://duchangjialideha.x.ddnsto.com/`），Nabu Casa 也在但国内被墙
- 用 **HACS** 装这个集成；仓库已配好 `hacs.json`
- 用户在同时用 **Claude Code** 和 **MiniMax Code (Mavis)**，两个工具都可能是接手方

## 用户是谁 / 怎么跟他协作

智能硬件产品经理，**不是程序员**。协作要点：

- **不会 SSH、不会命令行**。部署动作必须是"点几下 UI"
- 沟通用中文，代码/命令/变量名用英文
- **结论先行**，不要铺垫；不要奉承；方案有问题直接指出
- 他会在 HA 上做真实测试并把现象/日志贴回来 —— 这是你验证的唯一途径
- 他要的是"一次解决"而不是渐进式试探。所以本地验证做足，别让他反复重启

## 当前能力矩阵

| 设备 | HA 平台 | 状态 |
|---|---|---|
| 开关 / 插座 / 快捷面板 | `switch` | ✅ 可用 |
| 灯（开关型） | `light` (onoff) | ✅ 可用 |
| 灯（调光 + 调色温） | `light` (color_temp) | ✅ 可用 |
| 灯（调光 + 色温 + RGB，双模式） | `light` (color_temp + rgb) | ✅ v1.3.8 起可用 |
| 窗帘 / 窗帘电机 | `cover` | ✅ 可用 |
| 空调（mix schema） | `climate` | ✅ 可用（含摆风） |
| 空调（虚拟子设备 schema） | `climate` | ✅ 可用（自动风速档受限） |
| 地暖 | `climate` | ✅ 可用 |
| 新风 | `switch` | ⚠️ 能用，但应该升级成 `fan` |
| 温湿度 / 人体传感器 | `sensor` / `binary_sensor` | ✅ 可用 |
| Plug 实时功率 | `sensor` (power) | ✅ 可用（5s 轮询） |
| 红外空调伴侣 | `climate` | ⚠️ 实现了但用户无此设备，未验证 |
| 门锁 / 门铃 / 摄像头 / 场景 | — | ❌ 未实现 |

## 架构速记

```
挚算云  ←→  ZhisuanApi（OAuth + 401 自动重登）
              ↕
        ZhisuanCoordinator（60s 拉全量兜底 + 5s 拉 Plug 功率）
              ↖ ZhisuanWebhookView（云端推送 DeviceReport）
              ↓
        实体层（switch/light/cover/climate/sensor…，能力由 actionList 驱动）
```

**双数据源**：webhook 推送为主（1-2 秒延迟），60 秒轮询兜底。

## 踩过的坑（血泪教训，按重要性排序）

1. **官方 PDF 字段名大量错误** —— `SetColorTemperature` 要用 `value` 不是 `colorTemperature`；
   `SetColor` 要用小写 `red/green/blue`（大写直接返回 421）；空调 mode 的 1/2 是反的。
   **全部以 `docs/api-quirks.md` 为准。**

2. **401 自动重登一开始是失效的** —— 云端返回的是 HTTP 200 + `code:401`，
   如果 `_parse_response` 抛 `ZhisuanApiError`，外层 `except ZhisuanAuthError` 永远进不去。
   这个 bug 让"token 过期后所有控制静默失效"存活了很多个版本。

3. **webhook 白名单两套 schema 都要覆盖** —— 漏了 `setTemp`/`curTemp`/`speed` 导致
   "空调调到 25 度，卡片又回弹成 24"。

4. **空调有两套完全不同的字段名**（按父设备 ID 区分），不是一套。

5. **云端会谎报 actionList** —— 客厅灯带声称支持 RGB，实际没有，
   导致它被 HA 声明成彩色灯后"打不开"。

6. **HA 2026 破坏性变更** —— 删了 `ClimateEntityFeature.MODE`、`UnitOfIlluminance`，
   `ha_webhook.async_register` 只收函数。升级 HA 前先查这几项。

7. **灯的双模式（色温 + RGB）必须两个都声明** —— 只声明 RGB 会让色温控件消失。

详见 `docs/api-quirks.md`（11 章）和 `AGENTS.md` §6。

## 版本历史（关键节点）

| 版本 | 修了什么 |
|---|---|
| v1.0.0–1.0.6 | 打通 OAuth（`countryCode` 必传）、响应结构、homeId 走 header |
| v1.0.7–1.1.0 | webhook 注册（`hass.http.register_view`）、HA 2026 常量适配 |
| v1.1.x | 子设备 `via_device`、传感器只建有效属性、公网 URL 优先级 |
| v1.2.0–1.2.5 | 401 重登（此版实现有 bug，v1.3.1 才真修好）、虚拟子设备映射、清理脏 battery 实体 |
| v1.2.6–1.2.7 | Plug 实时功率（`QueryDisconnector` + 5s 后台轮询） |
| v1.3.0 | climate 摆风、TurnOn 防 race、debug 日志 |
| v1.3.1 | **401 自动重登真正生效** |
| v1.3.2 | 空调 mode 数值 1↔2 对调（与文档相反） |
| v1.3.3 | webhook 白名单补虚拟子设备字段（修状态回弹） |
| v1.3.4 | `SetWindSpeed` 字段按 schema 分流（`speed` 字符串 / `value` 数字） |
| v1.3.5 | `SetColor` 字段大写改小写 |
| v1.3.6 | 客厅灯带(2905) 能力校正 |
| v1.3.7 | 除湿模式 `DEHUMIDIFICATION` 字符串识别 |
| **v1.3.8** | **双模式灯（主卧灯带 2907/2891）支持色温** |

## 优先做的事（如果继续开发）

1. **新风升级成 `fan` 平台** —— 设备有风速档位，现在压成 `switch` 浪费了
2. **虚拟子设备"自动"风速档** —— 云端不认 `{"speed":"AUTO"}`，可以试
   `AdjustUp/DownWindSpeed` 步进模拟，或问挚算客服
3. **场景（Scene）实体** —— 用户有 4 个 `SceneTrigger` 快捷面板，现在只能当开关用
4. **门锁 / 门铃** —— 设备类型已知，未实现
5. **多 home 支持** —— 现在硬编码单 homeId

## 风险与注意事项

- **挚算云无 SLA、无文档质量保证**。任何"看起来该有"的字段都要实测
- **接口可能静默变化**。`GET /v1/device/{id}` 就是某天突然开始 500 的
- **用户在 GFW 内**。GitHub 推送要从能访问 GitHub 的机器做（本机可以，用户的家庭网络不行）
- **不要擅自改 HA 侧的行为假设**。用户已经调好了 Adaptive Lighting 等自动化，
  改动 `supported_color_modes` / 色温区间会直接影响它们

## 交接检查清单

- [x] 代码在 git 里，本地与远端同步（`v1.3.8`）
- [x] 云端 API 行为写成文档（`docs/api-quirks.md`）
- [x] 架构和数据流写成文档（`docs/architecture.md`）
- [x] 测试方法论 + 可复用工具（`docs/testing.md` + `tools/`）
- [x] 设备清单（`docs/device-inventory.md`）
- [x] AI Agent 规范（`AGENTS.md`）
- [x] 逻辑测试 47 条全过（`python3 tools/test_logic.py`）
- [ ] **挚算凭据**：需用户当面/私下交接，不在仓库里
- [ ] **GitHub 推送权限**：需用户给新 token 或加 collaborator
