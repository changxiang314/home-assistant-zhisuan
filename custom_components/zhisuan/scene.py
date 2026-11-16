"""Scene platform for ZhiSuan (挚算智联) cloud scenes.

The ZhiSuan cloud keeps "scenes" (场景) — named bundles of device actions.
A scene with ``manual=true`` is exactly what a person triggers on purpose
(离家模式 / 回家模式 / 睡眠模式 / 冷光 …), so it maps 1:1 onto a Home
Assistant scene entity: activate it and the cloud drives the devices, then
the resulting state changes come back through the webhook and update the
switch / light / cover entities on their own.

Scenes with ``manual=false`` are NOT exposed. Those are multi-control scenes
(several devices follow one device's state) or timer scenes — their trigger
lives in the cloud, so activating them from Home Assistant does nothing
meaningful.

Measured 2026-11-16 on homeId=185: 22 cloud scenes, 11 of them manual.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZhisuanCoordinator

_LOGGER = logging.getLogger(__name__)


def build_scene_name(scene: dict[str, Any]) -> str:
    """Build the display name for a cloud scene.

    The room name is appended because the same scene name can exist in
    several rooms — this home has two "中性光" scenes (厨房 and 卫生间),
    which would otherwise collide in the entity registry.
    """
    name = str(scene.get("sceneName") or f"场景 {scene.get('sceneId')}")
    room = scene.get("roomName")
    if room:
        return f"{name}（{room}）"
    return name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ZhiSuan scene entities from a config entry."""
    coordinator: ZhisuanCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    def _add_new(scenes: list[dict[str, Any]]) -> None:
        """Add entities for scenes discovered after startup."""
        async_add_entities(
            [ZhisuanSceneEntity(coordinator, scene) for scene in scenes]
        )

    # Scenes created in the ZhiSuan APP show up here within one refresh cycle.
    coordinator.set_scene_sink(_add_new)

    async_add_entities(
        [
            ZhisuanSceneEntity(coordinator, scene)
            for scene in coordinator.scenes.values()
        ]
    )


class ZhisuanSceneEntity(Scene):
    """A ZhiSuan cloud scene exposed as a Home Assistant scene."""

    _attr_should_poll = False
    # 挚算返回的 icon 是内部资源名（ic_com_lib_scene_default_nor_m），
    # HA 不认，所以这里固定用一个 mdi 图标。
    _attr_icon = "mdi:playlist-play"

    def __init__(
        self,
        coordinator: ZhisuanCoordinator,
        scene: dict[str, Any],
    ) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._scene = scene
        self._scene_id = int(scene["sceneId"])
        self._attr_unique_id = f"scene_{self._scene_id}"
        self._attr_name = build_scene_name(scene)

    # ------------------------------------------------------------------
    # 数据访问
    # ------------------------------------------------------------------
    @property
    def scene(self) -> dict[str, Any]:
        """Latest cloud payload for this scene (falls back to the startup copy)."""
        return self.coordinator.get_scene(self._scene_id) or self._scene

    @property
    def name(self) -> str:
        """Return the scene name (kept in sync with the cloud)."""
        return build_scene_name(self.scene)

    @property
    def available(self) -> bool:
        """Available while the scene is still known to the cloud."""
        return self.coordinator.get_scene(self._scene_id) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose cloud details for debugging."""
        scene = self.scene
        return {
            "scene_id": self._scene_id,
            "scene_type": scene.get("sceneType"),
            "room": scene.get("roomName"),
            "manual": scene.get("manual"),
            "action_count": len(scene.get("actionList") or []),
        }

    # ------------------------------------------------------------------
    # HA 生命周期
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates so a renamed scene updates itself."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the new state when the coordinator refreshed."""
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # 激活
    # ------------------------------------------------------------------
    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the scene — the cloud runs the action list.

        The cloud acknowledges immediately and drives the devices
        asynchronously; the device state changes arrive back through the
        webhook, so the light / switch / cover entities update on their own.
        """
        _LOGGER.info(
            "Activating ZhiSuan scene %s (%s)", self._scene_id, self.name
        )
        try:
            await self.coordinator.async_activate_scene(self._scene_id)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(
                f"执行挚算场景「{self.name}」失败: {err}"
            ) from err
