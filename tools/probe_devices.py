#!/usr/bin/env python3
"""挚算设备探测器 —— 排查"某类设备在 HA 里表现不对"的第一工具。

它直接打挚算云端，把每台设备的 type / model / parentId / actionList /
extension 关键字段打出来，并自动标注几种已知的特殊设备：
  - 双模式灯（同时 SetColor + SetColorTemperature）
  - 虚拟子设备（MultiInOneManager 下的 sub-AC）
  - mix schema 子设备（MultiInOnePanel 下）
  - 云端谎报能力的设备（见 CAPABILITY_OVERRIDES）

用法（凭据走环境变量，不要写在命令行里，避免进 shell history）：

    export ZS_CLIENT_ID=...
    export ZS_CLIENT_SECRET=...
    export ZS_USERNAME=...
    export ZS_PASSWORD=...
    export ZS_HOME_ID=185
    python3 tools/probe_devices.py                # 全量概览
    python3 tools/probe_devices.py --type Dimmer  # 只看某类
    python3 tools/probe_devices.py --id 2907      # 看单台完整 extension
    python3 tools/probe_devices.py --json out.json

依赖：aiohttp（pip install aiohttp）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

try:
    import aiohttp
except ImportError:
    sys.exit("需要 aiohttp：pip install aiohttp")

BASE = "https://app.aioteco.com/openApi"
DEFAULT_HOME_ID = "185"

# 与 custom_components/zhisuan/light.py 的 CAPABILITY_OVERRIDES 保持一致
KNOWN_CAPABILITY_LIES = {
    2905: {"SetColor"},
}
# 与 custom_components/zhisuan/climate.py 保持一致
VIRTUAL_PARENT_IDS = {2879}
MIX_PARENT_IDS = {2998}

EXT_SHOW_KEYS = [
    "turnOnOff", "on", "brightness", "colorTemperature", "color", "position",
    "operationMode", "workMode", "mode", "temperature", "setTemp",
    "currentTemperature", "curTemp", "windSpeed", "speed", "dynamicId",
    "isOnline", "battery", "humidity", "illuminance", "PM25", "co2",
    "nodeId", "eps",
]


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if not val:
        sys.exit(f"缺少环境变量 {name}（见脚本顶部用法说明）")
    return val


async def login(session: aiohttp.ClientSession) -> str:
    client_id = env("ZS_CLIENT_ID")
    async with session.post(
        f"{BASE}/oauth2/code",
        data={
            "username": env("ZS_USERNAME"),
            "userPassword": env("ZS_PASSWORD"),
            "client_id": client_id,
            "regionId": "CN",           # 必须是 CN，LOCAL/空字符串会 500
            "response_type": "code",
            "countryCode": "86",        # 少这个参数会 404
        },
    ) as resp:
        payload = await resp.json(content_type=None)
    if payload.get("code") != 200:
        sys.exit(f"OAuth code 失败: {payload}")
    code = payload["data"]["code"]

    async with session.post(
        f"{BASE}/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": env("ZS_CLIENT_SECRET"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": "http://",
        },
    ) as resp:
        payload = await resp.json(content_type=None)
    if "access_token" not in payload:
        sys.exit(f"OAuth token 失败: {payload}")
    return payload["access_token"]


async def fetch_all_devices(
    session: aiohttp.ClientSession, token: str, home_id: str
) -> list[dict[str, Any]]:
    """翻页拉全量设备。

    注意：homeId 走 HTTP header，不是 query 参数（放 query 会 500）。
    """
    headers = {
        "clientId": env("ZS_CLIENT_ID"),
        "authorization": token,
        "homeId": str(home_id),
        "language": "zh",
        "version": "1.0",
    }
    devices: list[dict[str, Any]] = []
    page = 1
    while True:
        async with session.get(
            f"{BASE}/v1/device", params={"page": page, "pageSize": 50}, headers=headers
        ) as resp:
            payload = await resp.json(content_type=None)
        data = payload.get("data")
        if not data:
            break  # 翻到尾页偶尔会返回 data=null，不要当错误
        pojo = data["pagePojo"]
        devices.extend(pojo.get("list") or [])
        if page >= pojo.get("pageTotal", 1):
            break
        page += 1
    return devices


def classify(device: dict[str, Any]) -> list[str]:
    """给设备打上已知的特殊标记。"""
    tags: list[str] = []
    actions = set(device.get("actionList") or [])
    parent_id = device.get("parentId")
    uid = device.get("userDeviceId")

    if "SetColor" in actions and "SetColorTemperature" in actions:
        tags.append("双模式灯(RGB+色温)")
    if parent_id in VIRTUAL_PARENT_IDS:
        tags.append("虚拟子设备(读父 eps[])")
    elif parent_id in MIX_PARENT_IDS:
        tags.append("mix 子设备")
    lies = KNOWN_CAPABILITY_LIES.get(uid)
    if lies:
        tags.append(f"能力谎报(已校正剔除 {'/'.join(sorted(lies))})")
    if (device.get("trait") or {}).get("isVirtual"):
        tags.append(f"多路 nodeId={device.get('nodeId')}")
    return tags


def ext_summary(device: dict[str, Any]) -> dict[str, Any]:
    ext = (device.get("cache") or {}).get("extension") or {}
    if not isinstance(ext, dict):
        return {"<raw>": str(ext)[:120]}
    out: dict[str, Any] = {}
    for key in EXT_SHOW_KEYS:
        if key not in ext:
            continue
        val = ext[key]
        if key == "eps" and isinstance(val, list):
            out[key] = f"[{len(val)} endpoints: " + ", ".join(
                f"ep{ep.get('epNum')}" for ep in val
            ) + "]"
        else:
            out[key] = val
    return out


def print_device(device: dict[str, Any], *, verbose: bool) -> None:
    uid = device.get("userDeviceId")
    tags = classify(device)
    tag_str = f"   [{' | '.join(tags)}]" if tags else ""
    print(
        f"\n#{uid}  {device.get('deviceName', '?')}"
        f"  type={device.get('type')}  model={device.get('model')}"
        f"  parentId={device.get('parentId')}{tag_str}"
    )
    actions = device.get("actionList") or []
    print(f"   actionList({len(actions)}): {', '.join(actions)}")
    summary = ext_summary(device)
    print(f"   cache.extension: {json.dumps(summary, ensure_ascii=False)}")
    print(f"   isOnline: {(device.get('cache') or {}).get('isOnline')}")
    if verbose:
        full = (device.get("cache") or {}).get("extension") or {}
        print("   完整 extension:")
        print(json.dumps(full, ensure_ascii=False, indent=2))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", help="只显示某类设备，如 Dimmer / AirCondition")
    ap.add_argument("--id", type=int, help="只显示单台设备（打印完整 extension）")
    ap.add_argument("--json", help="把原始设备列表另存为 JSON")
    args = ap.parse_args()

    home_id = os.environ.get("ZS_HOME_ID", DEFAULT_HOME_ID)
    async with aiohttp.ClientSession() as session:
        token = await login(session)
        print(f"登录成功（homeId={home_id}）")
        devices = await fetch_all_devices(session, token, home_id)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(devices, fh, ensure_ascii=False, indent=2)
        print(f"原始数据已保存: {args.json}")

    selected = devices
    if args.id is not None:
        selected = [d for d in devices if d.get("userDeviceId") == args.id]
    elif args.type:
        selected = [d for d in devices if d.get("type") == args.type]

    print(f"\n设备总数 {len(devices)}，本次显示 {len(selected)}")
    for device in selected:
        print_device(device, verbose=args.id is not None)

    # 末尾给一张类型汇总表，方便快速看全貌
    if args.id is None:
        by_type: dict[str, int] = {}
        for device in devices:
            by_type[device.get("type", "?")] = by_type.get(device.get("type", "?"), 0) + 1
        print("\n" + "=" * 56)
        print("按类型汇总：")
        for dtype, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
            print(f"  {dtype:<26} {count}")
        flagged = [
            d for d in devices
            if "双模式灯(RGB+色温)" in classify(d)
            and "SetColor" not in KNOWN_CAPABILITY_LIES.get(d.get("userDeviceId"), set())
        ]
        if flagged:
            print("\n真·双模式灯（HA 需声明 color_temp + rgb）：")
            for device in flagged:
                print(f"  #{device['userDeviceId']} {device.get('deviceName')}")

        lying = [d for d in devices if d.get("userDeviceId") in KNOWN_CAPABILITY_LIES]
        if lying:
            print("\n能力谎报设备（actionList 说有 SetColor，实际硬件没有）：")
            for device in lying:
                print(f"  #{device['userDeviceId']} {device.get('deviceName')}")


if __name__ == "__main__":
    asyncio.run(main())
