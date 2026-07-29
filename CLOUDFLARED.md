# 挚算智联 · Cloudflared 实时推送部署

> **为什么需要这个**：要让挚算云主动推送设备状态到 HA，HA 必须能从公网访问到。Cloudflared 给你一个免费 https 隧道。

---

## 1. 装 Cloudflared Add-on（HA 商店里就有）

**Settings → Add-ons → Add-on Store** → 搜 **"Cloudflared"** → 点进 → **Install**

启动前在 **Configuration** 标签页填：

```yaml
additional_hosts: []
external_hostname: ""
log_level: info
metrics: true  # ← 重要，集成靠这个端口读公网 URL
```

**Save** → 切到 **Info** 标签页 → 打开 **Start on boot** 和 **Watchdog** → 点 **Start**

启动后**等 30 秒**，日志里会出现类似：
```
+----------------------------------------+
|  https://xxxx-yyyy-zzzz.trycloudflare.com  |
+----------------------------------------+
```
**这个 URL 就是你的公网入口**，先复制下来。

## 2. 验证隧道通了

浏览器访问：`https://xxxx-yyyy-zzzz.trycloudflare.com/`

正常情况：看到 HA 的登录页（或 401 Unauthorized）—— 这就说明公网能访问到 HA 了。

## 3. 集成自动接管

挚算智联集成**启动时**会自动：
- 问 Cloudflared 拿公网 URL
- 把 webhook URL（`https://<公网>/api/zhisuan/webhook/<hook_id>`）告诉挚算云
- 每 6 小时检查一次 URL 漂移，变了就重订

不用你手填任何东西。

## 4. 验证实时推送

物理开关一下设备 / 在挚算 APP 里点开灯 → 1 秒内 HA 这边应该跟着变。

如果延迟 > 5 秒：
- Settings → System → Logs → 搜 `cloudflared` 看隧道状态
- 看是否有 `Webhook registered: POST ...` 日志

## 常见坑

| 现象 | 原因 | 修法 |
|------|------|------|
| Cloudflared 启动后没 trycloudflare URL | 被 GFW 屏蔽了 | 改用 **永久隧道**（需 Cloudflare 账号 + 域名），见下 |
| 集成日志说 "Cannot determine public URL" | Cloudflared 没起 / metrics 端口不通 | Add-on 启动后等 30 秒；检查 metrics: true |
| 集成日志说 "Subscribe failed" | 公网 URL 没法从外网访问 | 浏览器访问 trycloudflare URL 看是否通 |
| HA 重启后 trycloudflare URL 变了 | 短隧道特性 | 集成会每 6h 检测重订；如想稳定，用永久隧道 |

## 永久隧道（可选，URL 不变）

1. Cloudflare 后台：零信任 → 网络 → Tunnels → 创建隧道
2. 名字随便起 → 复制 **token**
3. HA Cloudflared Add-on Configuration 改成：
   ```yaml
   external_hostname: "你的子域名.example.com"  # 你域名解析到 tunnel
   tunnel_token: "<从 Cloudflare 复制的 token>"
   metrics: true
   ```
4. Cloudflare 后台隧道里 Public Hostname 加一条：
   - Subdomain: 你想要的
   - Domain: 你的域名
   - Service: `http://homeassistant.local:8123`
5. Save + 重启 Add-on
