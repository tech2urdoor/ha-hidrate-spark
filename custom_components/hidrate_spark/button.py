"""What used to need the app open."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory

from .coordinator import HidrateCoordinator
from .entity import HidrateEntity


@dataclass(frozen=True, kw_only=True)
class HidrateButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[HidrateCoordinator], Awaitable[None]]


BUTTONS = (
    HidrateButtonDescription(
        key="find",
        name="Find",
        icon="mdi:lightbulb-on",
        press_fn=lambda c: c.async_find(),
    ),
    HidrateButtonDescription(
        key="sync_now",
        name="Sync now",
        icon="mdi:sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_sync(),
    ),
    # Off by default: one stray press would throw off every sip until it's recalibrated.
    HidrateButtonDescription(
        key="calibrate_empty",
        name="Calibrate empty",
        icon="mdi:cup-outline",
        entity_category=EntityCategory.SETTINGS,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_calibrate_empty(),
    ),
    HidrateButtonDescription(
        key="calibrate_full",
        name="Calibrate full",
        icon="mdi:cup",
        entity_category=EntityCategory.SETTINGS,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_calibrate_full(),
    ),
)


async def async_setup_entry(_hass, entry, async_add_entities) -> None:
    coordinator: HidrateCoordinator = entry.runtime_data
    async_add_entities(HidrateButton(coordinator, description) for description in BUTTONS)


class HidrateButton(HidrateEntity, ButtonEntity):
    entity_description: HidrateButtonDescription

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
