"""Pure helpers for decoding ZhiSuan cloud notifications (no HA imports).

这个模块故意不 import homeassistant，也不 import aiohttp：
``tools/test_logic.py`` 不造 HA 桩就能直接把它当普通 Python 模块加载，
保证下面这些"云端实测出来的字段名"永远有测试兜底（字段名一错，按键就哑了）。
"""
from __future__ import annotations

from collections import deque
from typing import Any

# 快捷按键面板（2x2 四键，瞬时触发）的推送（2026-11 实测，debug 日志抓包）：
#
#   {
#     "messageType": "DeviceReport",
#     "data": {
#       "identify": "ShortcutPanelActivedNotification",   ← 只有按键触发是这个
#       "userDeviceId": 2921,
#       "extension": {
#         "productId": 6014,                              ← 或 3056
#         "buttonId": "2",                                ← 第几个键（字符串！）
#         "times": {"buttonId": 1794829656157, ...},      ← 按下时间戳(ms)
#       }
#     }
#   }
#
# 同一族有两款面板，推送 schema 完全一样、identify 也一样，只有 productId 不同：
#   - 6014 四路快捷面板（2921 厨房 / 2872 客厅 / 2889 主卧 / 2884 卫生间）
#   - 3056 快捷按钮（4806 客厅，2026-11-16 实测 buttonId "1"/"2" 同 schema）
# 以后再加同族面板，把新 productId 加进下面的集合即可。
#
# 同一个面板的"电量/绑定"类上报（identify=BatteryAlarm）里也带 buttonId，
# 但那是**上一次按下留下的旧值**，必须靠 identify 过滤掉，否则会误触发。
IDENTIFY_SHORTCUT_PANEL_ACTIVED = "ShortcutPanelActivedNotification"
SHORTCUT_PANEL_PRODUCT_IDS: tuple[int, ...] = (6014, 3056)
SHORTCUT_PANEL_BUTTONS = 4

# 同一次按下，云端会重复推送 N 条完全相同的消息（实测 2 秒内 20+ 条，
# messageId 和 times.buttonId 都一样，只有 HTTP 到达时间不同）。
# 去重缓存只留最近 64 次按键，内存可忽略。
_DEDUPE_CACHE_SIZE = 64


def parse_button_press(payload: dict[str, Any]) -> dict[str, Any] | None:
    """把一条 webhook payload 解析成按键事件；不是快捷面板按键就返回 None。

    返回字段：user_device_id / button(1..4) / pressed_at / message_id / identify。
    """
    if payload.get("messageType") != "DeviceReport":
        return None
    data = payload.get("data") or {}
    if data.get("identify") != IDENTIFY_SHORTCUT_PANEL_ACTIVED:
        return None
    ext = data.get("extension") or {}
    try:
        product_id = int(ext.get("productId"))
    except (TypeError, ValueError):
        return None
    if product_id not in SHORTCUT_PANEL_PRODUCT_IDS:
        return None
    try:
        button = int(ext.get("buttonId"))
    except (TypeError, ValueError):
        return None
    if not 1 <= button <= SHORTCUT_PANEL_BUTTONS:
        return None
    user_device_id = data.get("userDeviceId")
    if user_device_id is None:
        return None
    times = ext.get("times") or {}
    return {
        "user_device_id": int(user_device_id),
        "button": button,
        "pressed_at": times.get("buttonId"),
        "message_id": payload.get("messageId"),
        "identify": data.get("identify"),
        "product_id": product_id,
    }


class ButtonPressDeduplicator:
    """把"同一次按下被云端重复推送"折叠成一次。

    不同的按键一定带新的按下时间戳 / messageId，所以用
    (userDeviceId, buttonId, times.buttonId) 做 key 是安全的。
    """

    def __init__(self, size: int = _DEDUPE_CACHE_SIZE) -> None:
        self._seen: deque[tuple[Any, ...]] = deque(maxlen=size)

    def is_duplicate(self, press: dict[str, Any]) -> bool:
        key = (
            press.get("user_device_id"),
            press.get("button"),
            press.get("pressed_at") or press.get("message_id"),
        )
        if key in self._seen:
            return True
        self._seen.append(key)
        return False
