"""Whether the bottle is charging, which Home Assistant also shows on the device's battery."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .coordinator import HidrateCoordinator
from .entity import HidrateEntity

CHARGING = BinarySensorEntityDescription(
    key="charging",
    name="Charging",
    device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities([HidrateChargingSensor(coordinator, CHARGING)])


class HidrateChargingSensor(HidrateEntity, BinarySensorEntity):
    """As of the last sync, and unknown until one has read it. Off once it's charged, as well as off the charger."""

    @property
    def is_on(self) -> bool | None:
        charge = self.coordinator.charge
        return None if charge is None else charge == "charging"
