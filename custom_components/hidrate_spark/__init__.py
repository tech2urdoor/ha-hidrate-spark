"""Reads a HidrateSpark bottle through Bluetooth proxies, so it needs neither the app nor the cloud."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HidrateCoordinator

PLATFORMS = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    async def _probe(_call: ServiceCall) -> ServiceResponse:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            return await entry.runtime_data.async_probe()
        raise HomeAssistantError("No bottle is set up")

    hass.services.async_register(DOMAIN, "probe", _probe, supports_response=SupportsResponse.ONLY)
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
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
