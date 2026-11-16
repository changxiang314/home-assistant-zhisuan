#!/usr/bin/env python3
"""挚算场景探测器 —— 看云端有哪些场景、能不能执行。

只读工具：不执行任何场景（执行会真的动家里的灯/窗帘/空调）。

用法（凭据走环境变量，不要写进命令行，避免进 shell history）：

    export ZS_CLIENT_ID=...
    export ZS_CLIENT_SECRET=...
    export ZS_USERNAME=...
    export ZS_PASSWORD=...
    export ZS_HOME_ID=185
    python3 tools/probe_scenes.py                # 全部场景
    python3 tools/probe_scenes.py --manual       # 只看手动场景（HA 里会暴露的那些）
    python3 tools/probe_scenes.py --id 160348    # 看单个场景的全部动作
    python3 tools/probe_scenes.py --json out.json

依赖：无（只用标准库）
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://app.aioteco.com/openApi"
DEFAULT_HOME_ID = "185"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if not val:
        sys.exit(f"缺少环境变量 {name}（见脚本顶部用法说明）")
    return val


def _req(method: str, path: str, *, headers=None, form=None, timeout: int = 30):
    body = None
    hdrs = {"language": "zh", "version": "1.0"}
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        hdrs["content-type"] = "application/x-www-form-urlencoded"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def login() -> str:
    client_id = env("ZS_CLIENT_ID")
    _, raw = _req(
        "POST",
        "/oauth2/code",
        form={
            "username": env("ZS_USERNAME"),
            "userPassword": env("ZS_PASSWORD"),
            "client_id": client_id,
            "regionId": "CN",      # 必须是 CN
            "response_type": "code",
            "countryCode": "86",   # 少这个会 404
        },
    )
    payload = json.loads(raw)
    if payload.get("code") != 200:
        sys.exit(f"OAuth code 失败: {raw[:300]}")
    code = payload["data"]["code"]

    _, raw = _req(
        "POST",
        "/oauth2/token",
        form={
            "client_id": client_id,
            "client_secret": env("ZS_CLIENT_SECRET"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": "http://",
        },
    )
    payload = json.loads(raw)
    if "access_token" not in payload:
        sys.exit(f"OAuth token 失败: {raw[:300]}")
    return payload["access_token"]


def _headers(token: str, home_id: str) -> dict[str, str]:
    return {
        "clientId": env("ZS_CLIENT_ID"),
        "authorization": token,
        "homeId": str(home_id),
        "language": "zh",
        "version": "1.0",
    }


def fetch_scenes(token: str, home_id: str, *, manual: bool | None = None):
    path = "/v1/scene"
    if manual is not None:
        path += f"?manual={'true' if manual else 'false'}"
    _, raw = _req("GET", path, headers=_headers(token, home_id))
    payload = json.loads(raw)
    if payload.get("code") != 200:
        sys.exit(f"拉场景失败: {raw[:300]}")
    return (payload.get("data") or {}).get("sceneList") or []


def describe(scene: dict) -> str:
    """把一个场景的动作列表压成一行人话。"""
    parts = []
    for action in scene.get("actionList") or []:
        atype = action.get("actionType")
        aname = action.get("actionName")
        device = action.get("device") or {}
        target = device.get("deviceName") or action.get("userDeviceId")
        ext = action.get("extension")
        if isinstance(ext, list):
            rooms = "/".join(str(r.get("roomName")) for r in ext)
            target = f"[{rooms}]"
        parts.append(f"{aname}→{target}")
    return ", ".join(parts) if parts else "(无动作)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual", action="store_true", help="只看手动场景")
    ap.add_argument("--id", type=int, help="看单个场景（打印完整 JSON）")
    ap.add_argument("--json", help="把原始场景列表另存为 JSON")
    args = ap.parse_args()

    home_id = os.environ.get("ZS_HOME_ID", DEFAULT_HOME_ID)
    token = login()
    print(f"登录成功（homeId={home_id}）")

    manual = True if args.manual else None
    scenes = fetch_scenes(token, home_id, manual=manual)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(scenes, fh, ensure_ascii=False, indent=2)
        print(f"原始数据已保存: {args.json}")

    if args.id is not None:
        for scene in scenes:
            if scene.get("sceneId") == args.id:
                print(json.dumps(scene, ensure_ascii=False, indent=2))
        return

    manual_count = sum(1 for s in scenes if s.get("manual") is True)
    print(f"\n场景总数 {len(scenes)}（其中 manual=true {manual_count} 个）")
    print("=" * 72)
    for scene in scenes:
        flag = "★" if scene.get("manual") else " "
        triggers = ",".join(
            str(t.get("triggerType")) for t in (scene.get("triggerList") or [])
        ) or "-"
        print(
            f"{flag} #{scene.get('sceneId'):<8} {scene.get('sceneName'):<10} "
            f"type={scene.get('sceneType'):<12} room={scene.get('roomName'):<5} "
            f"triggers={triggers}"
        )
        print(f"      {describe(scene)}")
    print("=" * 72)
    print("★ = manual=true，HA 里会做成可点击的场景实体")


if __name__ == "__main__":
    main()
