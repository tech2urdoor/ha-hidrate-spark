"""The glow colour, which is the one setting that lives on the bottle, and the glow style, which doesn't."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory

from . import protocol
from .const import CONF_LIGHT
from .coordinator import HidrateCoordinator
from .entity import HidrateEntity

# Off by default: this writes to the bottle's own settings, which is worth choosing to do.
COLOUR = SelectEntityDescription(
    key="glow_colour",
    name="Glow colour",
    icon="mdi:palette",
    entity_category=EntityCategory.CONFIG,
    entity_registry_enabled_default=False,
    options=list(protocol.GLOW_COLOURS),
)

GLOW_STYLE = SelectEntityDescription(
    key=CONF_LIGHT,
    name="Glow style",
    icon="mdi:brightness-6",
    entity_category=EntityCategory.CONFIG,
    translation_key="glow_light",
    options=list(protocol.LIGHTS),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(
        [HidrateColourSelect(coordinator, COLOUR), HidrateGlowStyleSelect(coordinator, GLOW_STYLE)]
    )


class HidrateColourSelect(HidrateEntity, SelectEntity):
    """Unknown until it's been set here. A sync never reads the colour, so it can't put sips at risk."""

    @property
    def current_option(self) -> str | None:
        return self.coordinator.colour

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_colour(option)


class HidrateGlowStyleSelect(HidrateEntity, SelectEntity):
    """The same setting as the Configure screen."""

    @property
    def current_option(self) -> str:
        return self.coordinator.option(CONF_LIGHT)

    async def async_select_option(self, option: str) -> None:
        self.coordinator.async_set_option(CONF_LIGHT, option)
