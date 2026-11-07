"""How often reminders come and what they're pacing towards. The same settings as the Configure screen."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolume

from .const import CONF_FREQUENCY_MIN, CONF_GOAL_ML, FREQUENCY_RANGE, GOAL_RANGE
from .coordinator import HidrateCoordinator
from .entity import HidrateEntity

NUMBERS = (
    NumberEntityDescription(
        key=CONF_FREQUENCY_MIN,
        name="Minutes between reminders",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=FREQUENCY_RANGE[0],
        native_max_value=FREQUENCY_RANGE[1],
        native_step=FREQUENCY_RANGE[2],
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key=CONF_GOAL_ML,
        name="Daily goal",
        icon="mdi:bullseye-arrow",
        entity_category=EntityCategory.CONFIG,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        native_min_value=GOAL_RANGE[0],
        native_max_value=GOAL_RANGE[1],
        native_step=GOAL_RANGE[2],
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(HidrateNumber(coordinator, description) for description in NUMBERS)


class HidrateNumber(HidrateEntity, NumberEntity):
    @property
    def native_value(self) -> float:
        return float(self.coordinator.option(self.entity_description.key))

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.async_set_option(self.entity_description.key, value)
