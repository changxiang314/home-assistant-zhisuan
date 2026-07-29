"""Constants for the ZhiSuan (挚算智联) integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "zhisuan"
MANUFACTURER: Final = "杭州挚算科技有限公司"
DEFAULT_NAME: Final = "挚算智联"

# ----- Configuration keys -----
CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_SECRET: Final = "client_secret"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_REGION: Final = "region"
CONF_HOME_ID: Final = "home_id"
CONF_ENVIRONMENT: Final = "environment"  # "dev" or "prod"

# ----- API endpoints -----
ENV_DEV: Final = "dev"
ENV_PROD: Final = "prod"

API_BASE_URLS: Final = {
    ENV_DEV: "https://apptest.aioteco.com/openApi",
    ENV_PROD: "https://app.aioteco.com/openApi",
}

# OAuth 端点不带 /openApi 前缀（业务 API 才带）
OAUTH_BASE_URLS: Final = {
    ENV_DEV: "https://apptest.aioteco.com",
    ENV_PROD: "https://app.aioteco.com",
}

DEFAULT_ENVIRONMENT: Final = ENV_DEV

# ----- OAuth -----
OAUTH_AUTHORIZE_URL: Final = "/oauth2/code"
OAUTH_TOKEN_URL: Final = "/oauth2/token"
REGISTER_URL: Final = "/v1/register"

# ----- REST endpoints -----
HOME_LIST_URL: Final = "/v1/home"
ROOM_LIST_URL: Final = "/v1/room"
DEVICE_LIST_URL: Final = "/v1/device"
DEVICE_BY_ID_URL: Final = "/v1/device/{user_device_id}"  # noqa: S105
DEVICE_CONTROL_URL: Final = "/v1/device/control"
SUBSCRIBE_URL: Final = "/v1/subscribe"
UNSUBSCRIBE_URL: Final = "/v1/subscribe/{home_id}"  # noqa: S105

# ----- HTTP request defaults -----
DEFAULT_TIMEOUT: Final = 30  # seconds
DEFAULT_LANGUAGE: Final = "zh"
API_VERSION: Final = "1.0"

# ----- Token -----
# 挚算 access_token 90 天 = 7,776,000 秒。提前 7 天刷。
TOKEN_REFRESH_MARGIN: Final = 7 * 24 * 3600

# ----- Webhook -----
# 集成启动时 / 每 N 秒检查一次 cloudflared URL 是否变化，变了就重新订阅
WEBHOOK_RESUBSCRIBE_INTERVAL: Final = 6 * 3600  # 6h
# Cloudflared 默认本地 API 端口（Cloudflared Add-on 默认）
CLOUDFLARED_METRICS_PORT: Final = 2000

# ----- Device actions (挚算 EDeviceAction) -----
ACTION_TURN_ON: Final = "TurnOn"
ACTION_TURN_OFF: Final = "TurnOff"
ACTION_TURN_ON_OFF: Final = "TurnOnOff"
ACTION_PAUSE: Final = "Pause"
ACTION_SET_POSITION: Final = "SetPosition"
ACTION_SET_MODE: Final = "SetMode"
ACTION_SET_COLOR_TEMP: Final = "SetColorTemperature"
ACTION_SET_BRIGHTNESS: Final = "SetBrightness"
ACTION_SET_BRIGHTNESS_COLOR_TEMP: Final = "SetBrightnessColorTemperature"
ACTION_SET_COLOR: Final = "SetColor"
ACTION_SET_REVERSE: Final = "SetReverse"
ACTION_SET_TEMPERATURE: Final = "SetTemperature"
ACTION_SET_WIND_SPEED: Final = "SetWindSpeed"
ACTION_SEND_DATA: Final = "SendData"

# ----- Device types (挚算 EDeviceType) — 关注的 -----
DEVICE_TYPE_HOST: Final = "Host"
DEVICE_TYPE_GATEWAY: Final = "Gateway"
DEVICE_TYPE_LIGHT: Final = "Light"
DEVICE_TYPE_PLUG: Final = "Plug"
DEVICE_TYPE_SENSOR: Final = "Sensor"
DEVICE_TYPE_DETECTOR: Final = "Detector"
DEVICE_TYPE_CURTAINS: Final = "Curtains"
DEVICE_TYPE_CURTAINS_MOTOR: Final = "CurtainsMotor"
DEVICE_TYPE_BUTTON: Final = "Button"
DEVICE_TYPE_DIMMER: Final = "Dimmer"
DEVICE_TYPE_SWITCH: Final = "Switch"
DEVICE_TYPE_INFRARED: Final = "Infrared"
DEVICE_TYPE_WEBCAM: Final = "Webcam"
DEVICE_TYPE_DOOR_LOCK: Final = "DoorLock"
DEVICE_TYPE_DOOR_BELL: Final = "DoorBell"
DEVICE_TYPE_AIR_CONDITION: Final = "AirCondition"
DEVICE_TYPE_AIR_CONDITION_MANAGER: Final = "AirConditionManager"
DEVICE_TYPE_FLOOR_HEATING: Final = "FloorHeating"
DEVICE_TYPE_AIR_FRESHER: Final = "AirFresher"
DEVICE_TYPE_AIR_PURIFIER: Final = "AirPurifier"
DEVICE_TYPE_AIR_MONITOR: Final = "AirMonitor"
DEVICE_TYPE_DISCONNECTOR: Final = "Disconnector"

# ----- 设备 type -> HA 平台域 -----
# 一个挚算 type 落到哪个 HA 平台；为空表示不创建 HA 实体
DEVICE_TYPE_TO_HA_PLATFORM: Final = {
    DEVICE_TYPE_SWITCH: "switch",
    DEVICE_TYPE_PLUG: "switch",
    DEVICE_TYPE_LIGHT: "light",
    DEVICE_TYPE_DIMMER: "light",
    DEVICE_TYPE_CURTAINS: "cover",
    DEVICE_TYPE_CURTAINS_MOTOR: "cover",
    DEVICE_TYPE_AIR_CONDITION: "climate",
    DEVICE_TYPE_AIR_CONDITION_MANAGER: "climate",
    DEVICE_TYPE_FLOOR_HEATING: "climate",
    DEVICE_TYPE_SENSOR: "sensor",
    DEVICE_TYPE_DETECTOR: "sensor",
    DEVICE_TYPE_AIR_MONITOR: "sensor",
    DEVICE_TYPE_INFRARED: "climate",  # 空调伴侣当作 climate
    # Host / Gateway / Button / Webcam / DoorLock / DoorBell / AirFresher / AirPurifier / Disconnector 暂不实现
}

# ----- Sensor / Detector 属性 → HA sensor 平台 + 字段 -----
# value 来自 device.cache.extension[key]
SENSOR_PROPERTY_DEFS: Final = {
    # 数值型 sensor
    "temperature": {"platform": "sensor", "device_class": "temperature", "unit": "°C", "multiplier": 1},
    "currentTemperature": {"platform": "sensor", "device_class": "temperature", "unit": "°C", "multiplier": 1},
    "humidity": {"platform": "sensor", "device_class": "humidity", "unit": "%", "multiplier": 1},
    "battery": {"platform": "sensor", "device_class": "battery", "unit": "%", "multiplier": 1},
    "illuminance": {"platform": "sensor", "device_class": "illuminance", "unit": "lx", "multiplier": 1},
    "PM25": {"platform": "sensor", "device_class": "pm25", "unit": "µg/m³", "multiplier": 1},
    "co2": {"platform": "sensor", "device_class": "carbon_dioxide", "unit": "ppm", "multiplier": 1},
    # 二值型 sensor
    "motionAlarmState": {"platform": "binary_sensor", "device_class": "motion", "on_value": 1},
    "contactState": {"platform": "binary_sensor", "device_class": "door", "on_value": 1},
    "waterSensorState": {"platform": "binary_sensor", "device_class": "moisture", "on_value": 1},
    "smokeSensorState": {"platform": "binary_sensor", "device_class": "smoke", "on_value": 1},
    "gasSensorState": {"platform": "binary_sensor", "device_class": "gas", "on_value": 1},
    "sosState": {"platform": "binary_sensor", "device_class": "safety", "on_value": 1},
}

# ----- Webhook message types (挚算 EMessageType) -----
MSG_TYPE_NORMAL: Final = "Normal"
MSG_TYPE_DEVICE_ACTION: Final = "DeviceAction"
MSG_TYPE_DEVICE_ONLINE: Final = "DeviceOnline"
MSG_TYPE_DEVICE_OFFLINE: Final = "DeviceOffline"
MSG_TYPE_DEVICE_REPORT: Final = "DeviceReport"
MSG_TYPE_NOTIFICATION: Final = "Notification"
MSG_TYPE_HOST_UNBIND: Final = "HostUnbind"
MSG_TYPE_DOOR_BELL: Final = "DoorBell"
MSG_TYPE_ALARM: Final = "Alarm"

# ----- 窗帘 operationMode 字段值 -----
CURTAIN_OPERATION_CLOSED: Final = 0
CURTAIN_OPERATION_OPEN: Final = 1
CURTAIN_OPERATION_STOPPED: Final = 2

# ----- 设备状态键（来自 device.cache.extension） -----
EXT_TURN_ON_OFF: Final = "turnOnOff"
EXT_BRIGHTNESS: Final = "brightness"
EXT_WHITE_BRIGHTNESS: Final = "whiteBrightness"
EXT_COLOR_TEMP: Final = "colorTemperature"
EXT_COLOR: Final = "color"
EXT_POSITION: Final = "position"
EXT_TEMPERATURE: Final = "temperature"
EXT_MODE: Final = "mode"
EXT_OPERATION_MODE: Final = "operationMode"
EXT_WIND_SPEED: Final = "windSpeed"
EXT_IS_ONLINE: Final = "isOnline"

# ----- 错误码 -----
ERR_INVALID_TOKEN: Final = 401
ERR_FORBIDDEN: Final = 403
ERR_NOT_FOUND: Final = 404
ERR_RATE_LIMITED: Final = 429
