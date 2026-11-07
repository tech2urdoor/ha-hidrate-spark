"""Whether reminders go to the bottle, and when they glow. The same settings as the Configure screen."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory

from .const import CONF_GLOW_ALWAYS, CONF_MANAGE_REMINDERS
from .coordinator import HidrateCoordinator
from .entity import HidrateEntity

SWITCHES = (
    SwitchEntityDescription(
        key=CONF_MANAGE_REMINDERS,
        name="Glow reminders",
        icon="mdi:bell-ring-outline",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=CONF_GLOW_ALWAYS,
        name="Glow at every reminder",
        icon="mdi:lightbulb-on-outline",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(HidrateSwitch(coordinator, description) for description in SWITCHES)


class HidrateSwitch(HidrateEntity, SwitchEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.option(self.entity_description.key))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        self.coordinator.async_set_option(self.entity_description.key, True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        self.coordinator.async_set_option(self.entity_description.key, False)
