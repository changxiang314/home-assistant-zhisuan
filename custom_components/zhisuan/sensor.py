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
    PERCENTAGE,
    UnitOfIlluminance,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_PROPERTY_DEFS
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

# 哪些 type 的设备走 sensor 平台
SENSOR_DEVICE_TYPES = {
    "Sensor", "Detector", "AirMonitor", "AirFresher", "AirPurifier",
    "Light", "Plug", "Switch", "Button",  # 这些也常有 battery 属性
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
    "Light": {"battery"},
    "Plug": {"battery"},
    "Switch": {"battery"},
    "Button": {"battery"},
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

    entities: list[ZhisuanSensorEntity] = []
    for user_device_id, device in coordinator.devices.items():
        dtype = device.get("type")
        if dtype not in SENSOR_DEVICE_TYPES:
            continue
        whitelist = DEVICE_PROP_WHITELIST.get(dtype, set())
        if not whitelist:
            continue
        for prop_name in whitelist:
            defn = SENSOR_PROPERTY_DEFS.get(prop_name)
            if defn is None or defn["platform"] != "sensor":
                continue
            entities.append(
                ZhisuanSensorEntity(coordinator, user_device_id, prop_name, defn)
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
        "illuminance": UnitOfIlluminance.LUX,
        "PM25": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        "co2": CONCENTRATION_PARTS_PER_MILLION,
    }.get(prop_name)
