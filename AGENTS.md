# AGENTS.md — 挚算智联 → Home Assistant 集成

> 给 AI Agent（Claude Code / Cursor / Codex / 任何工具）的项目规范。
> 遵循 [agents.md](https://agents.md/) 规范。
> **动手前先读这份，再读 `docs/api-quirks.md`。**

## 0. 30 秒了解项目

- **做什么**：把杭州挚算科技（ZhiSuan / aioteco）的小众智能家居云接进 Home Assistant
- **形态**：一个 HA custom component，通过 **HACS** 分发
- **当前版本**：v1.4.0（见 `custom_components/zhisuan/manifest.json`）
- **用户**：1 位真实用户（智能硬件 PM，非程序员），家里 66 台设备，HA OS 跑在 ARM 小主机
- **仓库**：https://github.com/changxiang314/home-assistant-zhisuan
- **本地路径**：`/Users/dcc/.minimax/workspace/zhisuan-ha 2`（注意目录名有空格和 `2`，是历史遗留）

**已可用**：开关 / 灯（调光+调色温+RGB）/ 窗帘 / 空调（两种 schema）/ 地暖 / 新风 / 传感器 / Plug 功率 / **云场景（11 个手动场景）**
**实现但用户未验证**：红外空调伴侣、门锁、门铃、摄像头

## 1. 必读顺序

1. `docs/api-quirks.md` —— 挚算云的真实行为（官方 PDF 有大量错误，**以这份为准**）
2. `docs/architecture.md` —— 模块职责、数据流、加新设备要动哪
3. `docs/testing.md` —— 怎么在本地验证，别去 HA 上试错
4. `docs/device-inventory.md` —— 用户 66 台设备里哪些是特殊的

## 2. 硬性约束

### 用户侧
- 用户**不是程序员**，不会 SSH / 不会命令行。所有部署动作必须是"点几下 UI"
- 交付渠道是 **HACS**：推 git commit → 用户在 HACS 点 Update → 重启 HA
- 用户 HA 的 WiFi **访问不了 GitHub**（GFW），但 HA 本身能连（HACS 正常）

### 测试纪律（最重要）
- **改一版要用户在 HA 上重启 15 分钟。所以绝不在 HA 上试错。**
- 顺序：`probe_devices.py` 看真实能力 → 直连云端验证 → `test_logic.py` 跑桩测试 → 才推版本
- 任何"能力声明 / 控制分发 / 字段名 / 数值映射"的改动，**必须**先过 `test_logic.py`

### 安全
- 挚算凭据（clientId / secret / 账号密码）**不进代码、不进 commit、不进日志**
- 测试脚本读环境变量；用完清理 `~/.config/zs-openapi/session.json`
- GitHub token 只通过环境变量传给 git，不写进 git config
- 不要给挚算云做压力测试（正常轮询频率见 `PLUG_POWER_POLL_INTERVAL`）

## 3. 代码规范

### 目录
```
custom_components/zhisuan/     ← 集成代码，直接映射到 HA /config/custom_components/zhisuan/
docs/                          ← 文档
tools/                         ← 本地工具（不入 HA）
```

**不要在 `custom_components/zhisuan/` 之外放运行时代码。**

### 命名
- HA domain：`zhisuan`（全小写，HA 要求）
- 类名前缀 `Zhisuan…`（`ZhisuanApi`、`ZhisuanLightEntity`）
- 常量 `UPPER_SNAKE_CASE`，私有属性下划线前缀
- 实体 `unique_id`：`{userDeviceId}` + 多路设备拼 `_{nodeId}`；Sensor 拆属性拼 `_{prop}`

### 注释
- **每个反直觉的取值都写清"为什么"，并标注来源**（实测 / 文档 §号 / CLI 源码）
- 云端行为和文档冲突时，注释里同时写两边，例如：
  ```python
  HVACMode.COOL: 2,    # 文档说 1，但实测 2（v1.3.2 用户反馈）
  ```
- 不确定的硬件数值标 `# 待验证`

### 能力判定
- 实体能力**完全由 `actionList` 决定**，不要"全开"
- 云端 `actionList` 会谎报 → 用 `CAPABILITY_OVERRIDES` 按 `userDeviceId` 剔除，**必须注释原因**

## 4. 常用命令

```bash
# 看设备真实能力（第一工具）
export ZS_CLIENT_ID=... ZS_CLIENT_SECRET=... ZS_USERNAME=... ZS_PASSWORD=... ZS_HOME_ID=185
python3 tools/probe_devices.py                    # 全量
python3 tools/probe_devices.py --id 2907          # 单台完整 extension
python3 tools/probe_devices.py --type Dimmer      # 按类型

# 看云端场景（只读，不执行）
python3 tools/probe_scenes.py                    # 全部 22 个场景
python3 tools/probe_scenes.py --manual           # 只看会暴露成 HA 实体的 11 个
python3 tools/probe_scenes.py --id 160348        # 单个场景的全部动作

# 逻辑桩测试（不需要 HA，不联网）
python3 tools/test_logic.py          # 只显示失败
python3 tools/test_logic.py -v       # 显示全部 72 条断言

# OAuth + 拉设备自测（老工具，功能被 probe_devices 覆盖）
python3 tools/selftest.py --client-id ... --client-secret ... --username ... --password ... --home-id 185

# 推版本
git add -A && git commit -m "fix(vX.Y.Z): <一句话>"
GH_TOKEN=<token> GIT_ASKPASS=<askpass脚本> git push https://x-access-token@github.com/changxiang314/home-assistant-zhisuan.git main
```

依赖：**无**（`probe_*.py` 和 `test_logic.py` 都只用标准库，不需要装 aiohttp / HA）。

## 5. 版本与提交

- 提交信息格式：`fix(vX.Y.Z): 症状 + 根因一句话`，正文写清根因、改动、验证方式
- 同时把 `manifest.json` 的 `version` 改成对应版本（HACS 靠 commit hash 检测更新，
  version 只是给人核对，但要保持一致）
- 推完告诉用户：**HACS 更新 → 重启 HA → 具体测哪几项**

## 6. 已知限制（不是 bug，别浪费时间修）

1. **虚拟子设备调风速到"自动"档无效** —— 云端不认 `{"speed": "AUTO"}`，只认 `HIGH/MID/LOW`
2. **云端 `keyValue` 复合键与单字段不一致** —— 挚算自己的 bug，靠 60s 轮询收敛
3. **`color` 字段永不清空** —— 切到色温模式后仍保留旧 RGB 值，不能用它判断当前模式
4. **红外空调伴侣状态不可读** —— 单向控制
5. **token 是单活的** —— 同账号同时只有一个有效 token，新的登录会作废旧的。
   调试脚本和 HA 会互相踢；**反复 401 是正常现象，不是集成坏了**。见 `docs/api-quirks.md` §13
6. **快捷面板（SceneTrigger）的"按键↔场景"绑定关系读不到** —— 面板没出现在任何场景的
   `triggerList` 里，无法自动生成"按这个键 = 执行这个场景"的按钮。见 §14

## 7. 待办 / 可改进

- [ ] **快捷面板按键事件进 HA**（用户重点需求）—— 需要用户在 HA 日志里确认云端推不推
      按键事件；轮询路径确定可行（`times.buttonId` 变化）但延迟 60s
- [ ] 虚拟子设备的"自动"风速档（需要问挚算客服，或改用 `AdjustUp/DownWindSpeed` 步进模拟）
- [ ] 新风（AirFresher）目前用 `switch` 平台，可升级成 `fan` 平台（有风速档位）
- [ ] 红外空调伴侣的状态回读
- [ ] `QueryStatisticsLog`（电量日志）对 Plug 返回 500，电量统计暂缺
- [ ] 门锁 / 门铃 / 摄像头未实现
- [ ] `PLUG_POWER_POLL_INTERVAL = 5s` 是否需要做成 UI 可配置
- [ ] 多套 home 支持（当前硬编码单 homeId）
- [ ] `__init__.py` 每次启动都重新 OAuth，会作废其它客户端的 token → 改为优先复用已存 token

## 8. 不要做的事

- 不要碰用户的 `.env` / token / 密钥
- 不要 `git push` 到别的分支或 force push
- 不要在没有真机验证的情况下把"字段名猜测"写进代码 —— 用 §4.2 的假 ID 技巧先确认
- 不要注释掉报错或加 `try/except: pass` 绕过
- 不要为了"看起来更完整"给纯开关设备加亮度/色温能力（会导致设备变 unavailable）
- 不要相信官方 PDF 的字段名，一律以实测为准

## 9. 排障入口

| 症状 | 先看 |
|---|---|
| 控制无效 / 401 | `docs/api-quirks.md` §1（401 必须抛 ZhisuanAuthError）、§13（token 单活） |
| 状态回弹 | `docs/api-quirks.md` §7（webhook 白名单漏字段） |
| 少一套控件 | 实体的 `supported_color_modes` / `supported_features` 属性 |
| 模式/风速方向反了 | `docs/api-quirks.md` §4.4 / §6 |
| 场景点不动 / 灯被莫名打开 | `docs/api-quirks.md` §12（执行场景有副作用） |
| 面板按键没反应 | `docs/api-quirks.md` §14（面板是发射器不是负载） |
| 一般性故障对照 | `docs/testing.md` 末尾"常见故障对照表" |
