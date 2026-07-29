# 挚算智联 → Home Assistant 自定义集成

把**挚算科技**（杭州挚算）的智能家居设备接入 Home Assistant。开关、调光灯、窗帘、空调、传感器全部覆盖，状态秒级同步。

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

---

## 项目结构

```
zhisuan-ha/
├── AGENTS.md           ← 项目规范（开发者视角）
├── README.md           ← 本文件（用户视角）
├── custom_components/
│   └── zhisuan/        ← HA 集成代码
└── tools/
    └── selftest.py     ← 自测脚本
```

---

## 已知限制

- **空调模式值**因设备而异，默认按常见空调的 1=COOL/2=HEAT/3=FAN 映射；如不一致在 `climate.py` 的 `DEFAULT_MODE_MAP` 改
- **多路设备**（如多路灯控）每个子路会建独立 HA 实体，命名带 `_1`/`_2`
- **红外空调伴侣**是单向的，HA 里看到的开关/温度状态不可信（设备没法回传）
- **API 限流**未在挚算文档中明确，集成默认每 60s 兜底轮询一次全量；如发现被限流请调大 coordinator 的 `DEFAULT_SCAN_INTERVAL`
- **Cloudflared trycloudflare URL 每次重启会变**，集成启动时会自动重订阅

---

## 升级

以后改代码后：
1. 重新把 `custom_components/zhisuan` 整个文件夹拖到 HA 共享盘
2. HA `Developer Tools` → `Restart` 重启
3. 不需要重新配置集成

---

有问题随时找我，把 HA 日志和 Cloudflared 日志贴过来我看。
