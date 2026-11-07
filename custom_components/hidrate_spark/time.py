"""Wake time and bedtime, which space the reminders out. The same settings as the Configure screen."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory

from .const import CONF_SLEEP, CONF_WAKE
from .coordinator import HidrateCoordinator
from .entity import HidrateEntity

TIMES = (
    TimeEntityDescription(
        key=CONF_WAKE,
        name="Wake time",
        icon="mdi:weather-sunset-up",
        entity_category=EntityCategory.CONFIG,
    ),
    TimeEntityDescription(
        key=CONF_SLEEP,
        name="Bedtime",
        icon="mdi:weather-night",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(HidrateTime(coordinator, description) for description in TIMES)


class HidrateTime(HidrateEntity, TimeEntity):
    @property
    def native_value(self) -> time:
        return time.fromisoformat(self.coordinator.option(self.entity_description.key))

    async def async_set_value(self, value: time) -> None:
        self.coordinator.async_set_option(self.entity_description.key, value.strftime("%H:%M:%S"))
