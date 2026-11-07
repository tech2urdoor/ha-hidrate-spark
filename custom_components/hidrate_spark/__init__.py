"""Reads a HidrateSpark bottle through Bluetooth proxies, so it needs neither the app nor the cloud."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HidrateCoordinator

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
ATTR_BOTTLE = "bottle"
ATTR_WEIGHT_SECONDS = "weight_seconds"


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    async def _probe(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("No bottle is set up")
        if chosen := call.data.get(ATTR_BOTTLE):
            entries = [entry for entry in entries if entry.entry_id == chosen]
            if not entries:
                raise HomeAssistantError("That bottle isn't set up")
        elif len(entries) > 1:
            raise HomeAssistantError("More than one bottle is set up, so choose which to probe")
        return await entries[0].runtime_data.async_probe(call.data.get(ATTR_WEIGHT_SECONDS, 0))

    hass.services.async_register(
        DOMAIN,
        "probe",
        _probe,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_BOTTLE): cv.string,
                vol.Optional(ATTR_WEIGHT_SECONDS, default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=120)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = HidrateCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(_hass: HomeAssistant, entry: ConfigEntry) -> None:
    entry.runtime_data.async_options_changed()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    # A pressed button's session isn't one the unload cancels. Left running beside a reloaded
    # coordinator, its save would overwrite sips the new one had read.
    await entry.runtime_data.async_wait_until_idle()
    return True
