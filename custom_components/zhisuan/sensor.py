"""Sensor platform for ZhiSuan (挚算智联).

One ZhiSuan device can expose many properties (temperature + humidity + battery + ...).
We split each property into its own HA entity so users can build automations on
any single value.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_PROPERTY_DEFS
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

# 哪些 type 的设备走 sensor 平台
# - 真传感器：Sensor / Detector / AirMonitor / AirFresher / AirPurifier
# - Plug：单独建一个 power sensor（数值从 QueryDisconnector 拉，
#   不在 device.cache.extension 里，单独走 coordinator 缓存）
SENSOR_DEVICE_TYPES = {
    "Sensor",
    "Detector",
    "AirMonitor",
    "AirFresher",
    "AirPurifier",
    "Plug",
}

# 设备类型 -> 关心的属性（不在这里的不创建 sensor 实体）
DEVICE_PROP_WHITELIST: dict[str, set[str]] = {
    "Sensor": set(SENSOR_PROPERTY_DEFS.keys()),
    "Detector": {"motionAlarmState", "contactState", "waterSensorState",
                 "smokeSensorState", "gasSensorState", "sosState",
                 "battery", "temperature", "humidity", "illuminance"},
    "AirMonitor": {"PM25", "co2", "temperature", "humidity"},
    "AirFresher": {"PM25", "co2", "temperature", "humidity", "battery"},
    "AirPurifier": {"PM25", "temperature", "humidity"},
    # Plug: 不走 extension 白名单，power 由专门类处理
    "Plug": set(),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan sensor entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[SensorEntity] = []
    for user_device_id, device in coordinator.devices.items():
        dtype = device.get("type")
        if dtype not in SENSOR_DEVICE_TYPES:
            continue
        whitelist = DEVICE_PROP_WHITELIST.get(dtype, set())
        # 只为设备实际有数据的属性建实体（看 extension 里有没有这个 key）
        ext = (device.get("cache") or {}).get("extension") or {}
        ext_keys: set[str] = set(ext.keys()) if isinstance(ext, dict) else set()
        for prop_name in whitelist:
            if prop_name not in ext_keys:
                continue  # 设备没这个属性就不建实体
            defn = SENSOR_PROPERTY_DEFS.get(prop_name)
            if defn is None or defn["platform"] != "sensor":
                continue
            entities.append(
                ZhisuanSensorEntity(coordinator, user_device_id, prop_name, defn)
            )
        # Plug: 单独建一个 power sensor（值从 coordinator.get_plug_power 拿）
        if dtype == "Plug":
            entities.append(
                ZhisuanPlugPowerSensor(coordinator, user_device_id)
            )
    async_add_entities(entities)


class ZhisuanSensorEntity(ZhisuanEntity, SensorEntity):
    """A single property of a ZhiSuan device, exposed as a HA sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
        prop_name: str,
        defn: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, user_device_id)
        self._prop_name = prop_name
        self._defn = defn
        self._attr_name = self._label_for(prop_name)
        # 装 SensorEntityDescription
        kwargs: dict[str, Any] = {
            "key": f"zhisuan_{prop_name}",
            "device_class": _ha_device_class_for(prop_name),
            "state_class": SensorStateClass.MEASUREMENT,
        }
        unit = _ha_unit_for(prop_name)
        if unit:
            kwargs["native_unit_of_measurement"] = unit
        self.entity_description = SensorEntityDescription(**kwargs)

    @property
    def unique_id(self) -> str:
        return f"{self._user_device_id}_{self._prop_name}"

    @property
    def native_value(self) -> float | int | None:
        val = self.ext.get(self._prop_name)
        if val is None:
            return None
        try:
            return self._defn.get("multiplier", 1) * float(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _label_for(prop_name: str) -> str:
        return _PROP_LABELS.get(prop_name, prop_name)


_PROP_LABELS: dict[str, str] = {
    "temperature": "温度",
    "currentTemperature": "当前温度",
    "humidity": "湿度",
    "battery": "电量",
    "illuminance": "光照",
    "PM25": "PM2.5",
    "co2": "CO₂",
}


def _ha_device_class_for(prop_name: str) -> SensorDeviceClass | None:
    return {
        "temperature": SensorDeviceClass.TEMPERATURE,
        "currentTemperature": SensorDeviceClass.TEMPERATURE,
        "humidity": SensorDeviceClass.HUMIDITY,
        "battery": SensorDeviceClass.BATTERY,
        "illuminance": SensorDeviceClass.ILLUMINANCE,
        "PM25": SensorDeviceClass.PM25,
        "co2": SensorDeviceClass.CO2,
    }.get(prop_name)


def _ha_unit_for(prop_name: str) -> str | None:
    return {
        "temperature": UnitOfTemperature.CELSIUS,
        "currentTemperature": UnitOfTemperature.CELSIUS,
        "humidity": PERCENTAGE,
        "battery": PERCENTAGE,
        "illuminance": LIGHT_LUX,
        "PM25": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        "co2": CONCENTRATION_PARTS_PER_MILLION,
    }.get(prop_name)


class ZhisuanPlugPowerSensor(ZhisuanEntity, SensorEntity):
    """Real-time power (W) for a ZhiSuan Plug device.

    The value comes from `QueryDisconnector`, which the coordinator calls once
    per refresh cycle and caches in `coordinator._plug_power[userDeviceId]`.
    The first refresh may return ``None`` until the first query completes.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "实时功率"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        user_device_id: int,
    ) -> None:
        super().__init__(coordinator, user_device_id)
        # 覆盖基类的 unique_id property（基类按 userDeviceId 算）
        self._power_unique_id = f"{user_device_id}_plug_power"

    @property
    def unique_id(self) -> str:
        return self._power_unique_id

    @property
    def native_value(self) -> float | None:
        return self.coordinator.get_plug_power(self._user_device_id)
