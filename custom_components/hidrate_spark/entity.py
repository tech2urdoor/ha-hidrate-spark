"""What every bottle entity shares."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN
from .coordinator import HidrateCoordinator


class HidrateEntity(Entity):
    """Stays available between syncs: the readings come from the saved record, not a live link."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: HidrateCoordinator, description: EntityDescription) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name="Water Bottle",
            manufacturer="HidrateSpark",
            sw_version=coordinator.firmware,
            serial_number=coordinator.serial,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
