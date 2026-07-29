"""Binary sensor platform for ZhiSuan (挚算智联)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SENSOR_PROPERTY_DEFS
from .coordinator import ZhisuanCoordinator
from .entity import ZhisuanEntity

_LOGGER = logging.getLogger(__name__)

BINARY_SENSOR_DEVICE_TYPES = {"Sensor", "Detector"}

BINARY_PROP_WHITELIST: dict[str, set[str]] = {
    "Sensor": {"motionAlarmState", "contactState", "waterSensorState",
               "smokeSensorState", "gasSensorState", "sosState"},
    "Detector": {"motionAlarmState", "contactState", "waterSensorState",
                 "smokeSensorState", "gasSensorState", "sosState"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan binary sensor entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities: list[ZhisuanBinarySensorEntity] = []
    for user_device_id, device in coordinator.devices.items():
        dtype = device.get("type")
        if dtype not in BINARY_SENSOR_DEVICE_TYPES:
            continue
        whitelist = BINARY_PROP_WHITELIST.get(dtype, set())
        for prop_name in whitelist:
            defn = SENSOR_PROPERTY_DEFS.get(prop_name)
            if defn is None or defn["platform"] != "binary_sensor":
                continue
            entities.append(
                ZhisuanBinarySensorEntity(
                    coordinator, user_device_id, prop_name, defn
                )
            )
    async_add_entities(entities)


class ZhisuanBinarySensorEntity(ZhisuanEntity, BinarySensorEntity):
    """A single binary property of a ZhiSuan device."""

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
        self._attr_name = _PROP_LABELS.get(prop_name, prop_name)
        self.entity_description = BinarySensorEntityDescription(
            key=f"zhisuan_{prop_name}",
            device_class=_HA_DEVICE_CLASSES.get(prop_name),
        )

    @property
    def unique_id(self) -> str:
        return f"{self._user_device_id}_{self._prop_name}"

    @property
    def is_on(self) -> bool | None:
        val = self.ext.get(self._prop_name)
        if val is None:
            return None
        try:
            return int(val) == int(self._defn.get("on_value", 1))
        except (TypeError, ValueError):
            return None


_PROP_LABELS: dict[str, str] = {
    "motionAlarmState": "人体",
    "contactState": "门磁",
    "waterSensorState": "水浸",
    "smokeSensorState": "烟感",
    "gasSensorState": "燃气",
    "sosState": "SOS",
}

_HA_DEVICE_CLASSES: dict[str, BinarySensorDeviceClass | None] = {
    "motionAlarmState": BinarySensorDeviceClass.MOTION,
    "contactState": BinarySensorDeviceClass.DOOR,
    "waterSensorState": BinarySensorDeviceClass.MOISTURE,
    "smokeSensorState": BinarySensorDeviceClass.SMOKE,
    "gasSensorState": BinarySensorDeviceClass.GAS,
    "sosState": BinarySensorDeviceClass.SAFETY,
}
