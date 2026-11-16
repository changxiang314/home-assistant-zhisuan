# 挚算智联 → Home Assistant 自定义集成

把**挚算科技**（杭州挚算）的智能家居设备接入 Home Assistant。开关、调光灯、窗帘、空调、传感器全部覆盖，状态秒级同步。

---

## 推荐安装方式：HACS

1. HACS → 右上角三点 → `Custom repositories`
2. 填 `https://github.com/changxiang314/home-assistant-zhisuan`，类别选 `Integration`
3. 搜索「挚算智联」→ Download
4. 重启 Home Assistant
5. `设置 → 设备与服务 → 添加集成`，搜「zhisuan」或「挚算智联」
6. 填 Client ID / Client Secret / 挚算账号 / 密码，环境选 `prod`

**以后升级**：HACS 里点 Update → 重启 HA。不用重新配置。

> 本文档下面的手工部署流程（Samba + Cloudflared）是 HACS 之前的做法，仅作参考 / 排障用。

---

## 你要准备的东西

- 一台跑 HA OS 完整版的 Home Assistant 主机（你已经有了）
- 挚算开放平台给的 **Client ID** 和 **Client Secret**
- 你的 **挚算账号**（邮箱/手机）和**密码**
- 你 mac 电脑（用来搬代码）

---

## 5 步搞定，全程不用敲命令

### 第 1 步：在 HA 里装 2 个小工具

打开 HA 网页界面，左边菜单：

`Settings`（设置）→ `Add-ons`（加载项）→ 右上 `Add-on Store`（加载项商店）

搜这两个，分别点 Install：

1. **Samba share**（让你 mac 电脑能"看到"HA 里的文件夹）
2. **Cloudflared**（给 HA 装个"门铃"，让挚算云能找到你的 HA）

两个都装好后：
- **Samba**：点 `Start` 启动，**记下它显示的用户名和密码**
- **Cloudflared**：先不急，下一步搞完再启动

### 第 2 步：把代码搬到你家 HA 主机

1. 找到你 HA 主机的 IP 地址（HA 界面 `Settings` → `System` → `Network` 里能看到，或者在路由器后台找）
2. mac 上打开 `Finder`（访达）→ 顶部菜单 `前往` → `连接服务器`
3. 输入 `smb://<你HA的IP>`（比如 `smb://192.168.1.50`）→ 点连接
4. 输刚才记下的 Samba 用户名密码
5. 你会看到 HA 共享盘打开，里面有个 `config` 文件夹
6. 把本项目里的 `custom_components/zhisuan` **整个文件夹**拖到 `config/custom_components/` 下面

完成后你的目录结构应该是：

```
config/
└── custom_components/
    └── zhisuan/      ← 你刚拖进来的
        ├── __init__.py
        ├── manifest.json
        ├── ... 其他文件
```

### 第 3 步：装"门铃"（Cloudflared）

回到 HA 的 Cloudflared Add-on 页面：

1. 点 `Start` 启动
2. 等几秒，点 `Log`（日志）标签页
3. 你会看到一行类似 `https://xxxx-xxxx-xxxx.trycloudflare.com` 的网址 — **复制这个 URL**

> 这个 URL 每次 HA 重启都会变，没关系，集成会自动告诉挚算云新地址。

### 第 4 步：重启 HA + 添加集成

1. HA 左侧 `Developer Tools` → 顶部 `Restart` 按钮 → 重启 HA
2. 等 HA 启动完（约 30 秒-1 分钟）
3. HA 左侧 `Settings` → `Devices & Services`
4. 右上角 `+ Add Integration` 按钮
5. 搜 "**zhisuan**" 或 "**挚算智联**" → 选它
6. 弹窗让你填：
   - Client ID
   - Client Secret
   - 挚算账号（邮箱/手机）
   - 密码
   - 环境：选 `dev`（开发环境）或 `prod`（生产环境，看你 clientId 是哪个）
7. 提交

**成功后**：HA 会蹦出一堆设备（你的开关/灯/窗帘/空调/传感器）。每个挚算房间会变成 HA 的"区域"（area）。

### 第 5 步：验证实时同步

测试一下双向同步：
1. 用挚算 APP 开一盏灯
2. 看 HA 里这盏灯是不是 1 秒内变成"开"状态
3. 在 HA 里点一下关
4. 看挚算 APP 里这盏灯是不是也灭了

**都通过就完事了 🎉**

---

## 故障排查

### 集成没出现在 Add Integration 列表里
- 检查 `custom_components/zhisuan/__init__.py` 文件在不在
- HA 一定要重启过（不是 Reload）
- 看 HA 日志：`Settings` → `System` → `Logs`，搜 "zhisuan"

### 添加集成时报"无法连接"
- 检查你的 HA 能不能访问外网（HA 界面 `Developer Tools` → `Services` → 调 `system_log` 看能不能上网）
- 试一下换一个环境（dev 改 prod，或反过来）

### 添加集成时报"账号密码错误"
- 先用挚算 APP 确认账号密码能登录
- 确认 Client ID / Secret 跟环境对得上（dev 的 clientId 配 dev 环境）

### 设备都出现了，但状态一直不变
- 看 Cloudflared 是不是还在跑（Add-on 页面看状态）
- 看你 HA 主机的 IP 是不是没变（Cloudflared 启动时读的是当时的网络状态）
- 看 HA 日志搜 "subscribe"，看订阅是否成功

### 状态延迟 30 秒以上
- 通常是 Cloudflared 没起来，集成在用兜底轮询
- 启动 Cloudflared 后会切到实时

### 想要固定的门铃 URL（不每次重启变）
- 注册 Cloudflare 账号 + 域名（约几十块/年）
- Cloudflared Add-on 配 named tunnel
- 改本项目 `webhook.py` 里的 `async_resolve_public_url`，优先用固定 URL

---

## 能接入什么设备

| 挚算 type | HA 实体 | 能做什么 |
|---|---|---|
| 智能开关、插座 | switch | 开/关 |
| 灯（开关型） | light | 开/关 |
| 灯（调光） | light | 开/关 + 亮度 |
| 灯（调色温） | light | 开/关 + 亮度 + 色温 |
| 灯（调色） | light | 开/关 + 亮度 + 色温 + RGB 颜色 |
| 窗帘、窗帘电机 | cover | 开/关/停 + 位置百分比 |
| 空调 | climate | 开关/制冷制热/温度/风速 |
| 空调伴侣（红外） | climate | 红外控制（状态不可读） |
| 地暖 | climate | 开关 + 温度 |
| 传感器（多属性） | 多个 sensor/binary_sensor | 温度/湿度/电量/光照/PM2.5/CO2/人/门磁/水浸/烟感/燃气/SOS |
| 四路快捷面板（6014） | event | 按键 1~4 的按下事件（v1.4.0+，自动化里用「事件触发」） |

---

## 用 6014 四路快捷面板的按键控制 HA（v1.4.0+）

每块 6014 面板会生成 1 个 **event 实体**（名字形如「厨房快捷面板按键」），
4 个物理按键分别对应事件类型 `button_1` ~ `button_4`。

> 第一次用之前先按几下按键，在 **开发者工具 → 状态** 里搜「快捷面板按键」，
> 看实体的 `event_type` 变化来确定实物按键编号（从左到右/上下，以实物为准）。

自动化示例（「事件」触发 → 执行动作）：

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.chu_fang_kuai_jie_mian_ban_an_jian   # 以实际实体 ID 为准
    options:
      event_type: button_2
actions:
  - action: light.turn_on
    target:
      entity_id: light.ke_ting_luo_di_deng
```

- 同一次按下会被云端重复推送 10+ 条，集成已去重：**一次按下只触发一次**
- 面板在挚算 APP 里原来绑定的场景照旧生效，HA 这边是额外旁路
- 日志里每次按键会打一行 `6014 面板按键：… button_N`，可用来确认是否收到

---

## 项目结构

```
zhisuan-ha/
├── HANDOFF.md          ← 交接说明（接手的人/工具先读这个）
├── AGENTS.md           ← 给 AI Agent 的项目规范
├── README.md           ← 本文件（用户视角：怎么装）
├── docs/
│   ├── api-quirks.md   ← 挚算云真实行为手册（官方文档有错，以这份为准）
│   ├── architecture.md ← 代码结构与数据流
│   ├── testing.md      ← 测试方法论
│   └── device-inventory.md  ← 设备实测清单
├── custom_components/
│   └── zhisuan/        ← HA 集成代码
└── tools/
    ├── probe_devices.py ← 设备能力探测器（排障第一工具）
    ├── test_logic.py    ← 逻辑桩测试（不需要装 HA）
    └── selftest.py      ← 早期自测脚本
```

---

## 已知限制

- **空调模式数值已按实测校准**（`climate.py` 的 `DEFAULT_MODE_MAP`，与官方文档相反）。
  换设备后如果模式方向不对，用 `tools/probe_devices.py` 重新探测后改这张表
- **灯的双色温语义与常识相反**：`colorTemperature` 0=暖光 2700K，100=冷光 6500K
- **虚拟子设备空调的风速「自动」档无效**：云端不认 `AUTO` 字符串，只认 高/中/低
- **多路设备**（如多路灯控）每个子路会建独立 HA 实体，命名带 `_1`/`_2`
- **红外空调伴侣**是单向的，HA 里看到的开关/温度状态不可信（设备没法回传）
- **Plug 功率靠 5 秒轮询**：云端不推送功率变化，只能主动查询
- **API 限流**未在挚算文档中明确；实测 5 秒轮询 1 台设备完全无压力
- **公网 Webhook URL 变化**：集成启动时会自动重新订阅，URL 漂移后 6 小时内自动收敛

---

## 升级

**HACS 用户**：HACS → 挚算智联 → Update → 重启 HA。不需要重新配置集成。

**手工部署的**：重新把 `custom_components/zhisuan` 整个文件夹拖到 HA 共享盘 → 重启 HA。

---

出问题时，把 HA 日志（`设置 → 系统 → 日志`，搜 "zhisuan"）和具体现象贴出来。

