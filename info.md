# 挚算智联 → Home Assistant 集成

把**挚算科技**（杭州挚算）的智能家居设备接入 Home Assistant。开关、调光灯、窗帘、空调、传感器全支持，状态秒级同步（WebSocket 推送）。

## 功能

- 智能开关、插座 → `switch`
- 灯（开关/调光/调色/调色温）→ `light`
- 窗帘、窗帘电机 → `cover`
- 空调、空调伴侣、地暖 → `climate`
- 传感器（温度/湿度/电量/光照/PM2.5/CO2/人/门磁/水浸/烟感/燃气）→ `sensor` / `binary_sensor`
- OAuth 2.0 完整流程 + 90 天 token 自动刷新
- Webhook 实时状态推送（秒级同步）

## 安装

1. 通过 HACS 添加本仓库（Custom repository）
2. 重启 Home Assistant
3. Settings → Devices & Services → Add Integration → 搜 "zhisuan" / "挚算智联"
4. 填写 Client ID、Client Secret、挚算账号、密码

## 文档

完整文档见 [README](https://github.com/dcc/zhisuan-ha/blob/main/README.md)。
