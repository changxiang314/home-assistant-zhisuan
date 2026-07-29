"""Self-test for ZhiSuan API client.

Usage:
  # Mock mode (no real credentials, runs a local fake server)
  python tools/selftest.py --mock

  # Real mode (requires credentials)
  python tools/selftest.py \\
    --client-id <id> --client-secret <secret> \\
    --username <email> --password <pwd> \\
    --home-id <home_id>

Mock mode verifies the API client end-to-end (OAuth, list, control, subscribe).
Real mode also works against the production cloud, useful for live debugging.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Load api.py and const.py directly (without going through the package
# __init__.py, which imports homeassistant).
import importlib.util
import types

ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "custom_components" / "zhisuan"


def _load(mod_name: str, filename: str):
    """Load a module by absolute path; register it under mod_name."""
    spec = importlib.util.spec_from_file_location(
        mod_name, filename
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


const = _load("zhisuan.const", str(PKG_DIR / "const.py"))
api_mod = _load("zhisuan.api", str(PKG_DIR / "api.py"))  # noqa: F841


# Pre-register a stub package so relative imports inside const.py work
pkg_stub = types.ModuleType("zhisuan")
pkg_stub.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
sys.modules["zhisuan"] = pkg_stub

ZhisuanApi = api_mod.ZhisuanApi
ZhisuanApiError = api_mod.ZhisuanApiError
ZhisuanAuthError = api_mod.ZhisuanAuthError
API_BASE_URLS = const.API_BASE_URLS
ENV_DEV = const.ENV_DEV
ENV_PROD = const.ENV_PROD

import aiohttp  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("selftest")


# ----------------------------------------------------------------------
# Mock server
# ----------------------------------------------------------------------
async def _start_mock_server() -> tuple[str, dict[str, str]]:
    """Start an aiohttp server that mimics the ZhiSuan API.
    Returns (base_url, in-memory state dict)."""
    from aiohttp import web

    state: dict[str, object] = {
        "tokens": {
            "test_access_token": "AT_test123",
            "test_refresh_token": "RT_test456",
        },
        "subscribes": {},  # home_id -> notify_uri
        "device_states": {5988: {"turnOnOff": False}},  # 设备状态记忆
    }

    async def code(request: web.Request) -> web.Response:
        data = await request.post()
        if data.get("client_id") != "test_client_id":
            return web.json_response({"code": 401, "info": "bad client_id"}, status=401)
        if data.get("username") != "test_user":
            return web.json_response({"code": 401, "info": "bad user"}, status=401)
        return web.json_response(
            {
                "code": 200,
                "info": "SUCCESS",
                "data": {"code": "test_code_123", "openId": "open_1"},
            }
        )

    async def token(request: web.Request) -> web.Response:
        data = await request.post()
        grant = data.get("grant_type")
        if grant == "authorization_code":
            if data.get("code") != "test_code_123":
                return web.json_response(
                    {"error": "invalid_grant", "error_description": "bad code"},
                    status=400,
                )
            return web.json_response(
                {
                    "access_token": "test_access_token",
                    "token_type": "bearer",
                    "refresh_token": "test_refresh_token",
                    "expires_in": 7776000,
                }
            )
        if grant == "refresh_token":
            return web.json_response(
                {
                    "access_token": "test_access_token_2",
                    "token_type": "bearer",
                    "refresh_token": "test_refresh_token_2",
                    "expires_in": 7776000,
                }
            )
        return web.json_response(
            {"error": "unsupported_grant_type"}, status=400
        )

    async def homes(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        return web.json_response(
            {
                "code": 200,
                "info": "SUCCESS",
                "data": {"list": [{"homeId": 6, "homeName": "测试家"}]},
            }
        )

    async def rooms(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        return web.json_response(
            {
                "code": 200,
                "info": "SUCCESS",
                "data": [
                    {"roomId": 4580, "roomName": "客厅"},
                    {"roomId": 4581, "roomName": "卧室"},
                ],
            }
        )

    async def device_list(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        return web.json_response(
            {
                "code": 200,
                "info": "SUCCESS",
                "data": {
                    "page": 1,
                    "pageSize": 50,
                    "pageTotal": 1,
                    "dataTotal": 3,
                    "list": [
                        {
                            "userDeviceId": 5988,
                            "deviceName": "客厅吊灯",
                            "deviceMac": "00:12:4B:00:1A:1A:1A:1A",
                            "state": True,
                            "homeId": 6,
                            "roomId": 4580,
                            "roomName": "客厅",
                            "model": "102",
                            "type": "Light",
                            "trait": {"netType": "ZigBee", "isVirtual": False},
                            "actionList": [
                                "TurnOn",
                                "TurnOff",
                                "SetBrightness",
                                "SetColorTemperature",
                            ],
                            "cache": {
                                "isOnline": "true",
                                "updateTime": int(time.time() * 1000),
                                "extension": {
                                    "turnOnOff": False,
                                    "brightness": 50,
                                    "colorTemperature": 30,
                                },
                            },
                        },
                        {
                            "userDeviceId": 5989,
                            "deviceName": "卧室窗帘",
                            "deviceMac": "00:12:4B:00:1A:1A:1A:1B",
                            "state": True,
                            "homeId": 6,
                            "roomId": 4581,
                            "roomName": "卧室",
                            "model": "201",
                            "type": "Curtains",
                            "trait": {"netType": "ZigBee"},
                            "actionList": [
                                "TurnOn",
                                "TurnOff",
                                "Pause",
                                "SetPosition",
                            ],
                            "cache": {
                                "isOnline": "true",
                                "updateTime": int(time.time() * 1000),
                                "extension": {
                                    "operationMode": 0,
                                    "position": 0,
                                },
                            },
                        },
                        {
                            "userDeviceId": 5990,
                            "deviceName": "客厅温度计",
                            "deviceMac": "00:12:4B:00:1A:1A:1A:1C",
                            "state": True,
                            "homeId": 6,
                            "roomId": 4580,
                            "roomName": "客厅",
                            "model": "301",
                            "type": "Sensor",
                            "trait": {"netType": "ZigBee", "isBattery": True},
                            "actionList": [],
                            "cache": {
                                "isOnline": "true",
                                "updateTime": int(time.time() * 1000),
                                "extension": {
                                    "temperature": 24.5,
                                    "humidity": 60,
                                    "battery": 85,
                                },
                            },
                        },
                    ],
                },
            }
        )

    async def device_control(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        payload = await request.json()
        dev_id = payload.get("userDeviceId")
        name = payload.get("name")
        ext = payload.get("extension") or {}
        # 更新记忆中的状态
        if dev_id in state["device_states"]:
            if name == "TurnOn":
                state["device_states"][dev_id]["turnOnOff"] = True
            elif name == "TurnOff":
                state["device_states"][dev_id]["turnOnOff"] = False
        return web.json_response({"code": 200, "info": "SUCCESS", "data": {}})

    async def subscribe(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        payload = await request.json()
        state["subscribes"][payload["homeId"]] = payload["notifyUri"]
        return web.json_response({"code": 200, "info": "SUCCESS", "data": {}})

    async def unsubscribe(request: web.Request) -> web.Response:
        if request.headers.get("authorization") != "test_access_token":
            return web.json_response({"code": 401}, status=401)
        home_id = request.match_info["home_id"]
        state["subscribes"].pop(home_id, None)
        return web.json_response({"code": 200, "info": "SUCCESS", "data": {}})

    app = web.Application()
    app.router.add_post("/openApi/oauth2/code", code)
    app.router.add_post("/openApi/oauth2/token", token)
    app.router.add_get("/openApi/v1/home", homes)
    app.router.add_get("/openApi/v1/room", rooms)
    app.router.add_get("/openApi/v1/device", device_list)
    app.router.add_post("/openApi/v1/device/control", device_control)
    app.router.add_post("/openApi/v1/subscribe", subscribe)
    app.router.add_delete("/openApi/v1/subscribe/{home_id}", unsubscribe)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)  # random port
    await site.start()
    # 找实际端口
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
    base_url = f"http://127.0.0.1:{port}/openApi"
    _LOGGER.info("Mock server running at %s", base_url)
    return base_url, {"_state": state, "_runner": runner}


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
async def _run_checks(api: ZhisuanApi, home_id: int) -> None:
    """End-to-end check using the API client."""
    print("\n[1] Login (OAuth)...")
    await api.async_login("test_user", "test_pwd")
    assert api.has_token, "no token after login"
    print("    OK access_token =", api.access_token[:20], "...")

    print("\n[2] Get homes...")
    homes = await api.async_get_homes()
    print(f"    OK {len(homes)} homes, first = {homes[0]}")

    print("\n[3] Get rooms...")
    rooms = await api.async_get_rooms(home_id)
    print(f"    OK {len(rooms)} rooms, first = {rooms[0]}")

    print("\n[4] Get all devices (paged)...")
    devices = await api.async_get_all_devices(home_id)
    print(f"    OK {len(devices)} devices:")
    for d in devices:
        print(
            f"      - [{d['type']}] id={d['userDeviceId']} "
            f"name={d['deviceName']!r} actions={d.get('actionList')}"
        )
        print(f"        ext = {d.get('cache', {}).get('extension')}")

    print("\n[5] Control device (TurnOn 5988)...")
    await api.async_control_device(5988, "TurnOn", home_id=home_id)
    print("    OK control request accepted")

    print("\n[6] Subscribe webhook...")
    await api.async_subscribe(home_id, "https://example.com/hook")
    print("    OK subscribe accepted")

    print("\n[7] Unsubscribe...")
    await api.async_unsubscribe(home_id)
    print("    OK unsubscribe accepted")

    print("\n[8] Refresh token (force)...")
    # 强制过期一下
    api._token_expires_at = 0  # type: ignore[attr-defined]
    await api.async_refresh_tokens()
    assert api.access_token == "test_access_token_2", "token did not refresh"
    print("    OK refreshed access_token =", api.access_token)

    print("\n✅ All checks passed.")


async def main_async(args: argparse.Namespace) -> int:
    home_id = args.home_id or 6
    base_url_override: str | None = None
    runner = None
    try:
        if args.mock:
            base_url_override, mock_state = await _start_mock_server()
            runner = mock_state["_runner"]
            client_id = "test_client_id"
            client_secret = "test_secret"
            env = ENV_DEV
        else:
            if not all(
                [args.client_id, args.client_secret, args.username, args.password]
            ):
                print(
                    "ERROR: real mode requires --client-id --client-secret "
                    "--username --password",
                    file=sys.stderr,
                )
                return 2
            client_id = args.client_id
            client_secret = args.client_secret
            env = args.environment

        async with aiohttp.ClientSession() as session:
            api = ZhisuanApi(
                client_id=client_id,
                client_secret=client_secret,
                session=session,
                environment=env,
            )
            if base_url_override:
                # Override base URL for mock
                api._base_url = base_url_override  # type: ignore[attr-defined]

            await _run_checks(api, home_id)
        return 0
    except ZhisuanAuthError as e:
        print(f"\n❌ AUTH FAILED: {e}")
        return 1
    except ZhisuanApiError as e:
        print(f"\n❌ API ERROR: {e}")
        return 1
    finally:
        if runner is not None:
            await runner.cleanup()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mock", action="store_true", help="run against a local fake server")
    p.add_argument("--client-id", help="OAuth client_id")
    p.add_argument("--client-secret", help="OAuth client_secret")
    p.add_argument("--username", help="ZhiSuan account")
    p.add_argument("--password", help="ZhiSuan password")
    p.add_argument(
        "--environment", default=ENV_PROD, choices=[ENV_DEV, ENV_PROD]
    )
    p.add_argument("--home-id", type=int, help="home id to query")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
