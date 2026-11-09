"""Offering to put the bottle in Bermuda, so Home Assistant can say which room it's in.

Bermuda (a separate integration, https://github.com/agittins/bermuda) only tracks the devices picked in its own
settings. This goes through those settings the way a person would: its options flow, the Select Devices step, with
the bottle added to what's already picked. That way Bermuda checks the change and reloads itself. Its settings aren't
meant for other integrations, so anything unexpected stops here, changes nothing, and the user is told how to do it
by hand.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "bermuda"
STEP = "selectdevices"
FIELD = "configured_devices"


def _entry(hass: HomeAssistant) -> Any | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


def available(hass: HomeAssistant) -> bool:
    """Whether to offer it at all: Bermuda installed and running."""
    return _entry(hass) is not None


async def async_track(hass: HomeAssistant, address: str) -> bool:
    """Adds the bottle to Bermuda's devices. True if it's there afterwards, False if it has to be done by hand."""
    entry = _entry(hass)
    if entry is None:
        return False
    address = address.upper()
    picked = list(entry.options.get(FIELD, []))
    if address in picked:
        return True
    flows = hass.config_entries.options
    flow_id = None
    try:
        result = await flows.async_init(entry.entry_id)
        flow_id = result.get("flow_id")
        if result.get("type") == "menu":
            if STEP not in (result.get("menu_options") or []):
                raise LookupError(f"no {STEP} in its settings")
            result = await flows.async_configure(flow_id, {"next_step_id": STEP})
        if result.get("type") != "form" or result.get("step_id") != STEP:
            raise LookupError(f"its settings went to {result.get('type')} {result.get('step_id')}")
        result = await flows.async_configure(flow_id, {FIELD: [*picked, address]})
        if result.get("type") != "create_entry":
            raise LookupError(f"its settings answered {result.get('type')} {result.get('errors')}")
    except Exception as err:  # noqa: BLE001 - another integration's flow can fail any way at all
        _LOGGER.info("Couldn't add the bottle to Bermuda, so setup will say how: %s", err)
        if flow_id is not None:
            try:
                flows.async_abort(flow_id)
            except Exception:  # noqa: BLE001 - already finished or gone
                pass
        return False
    return True
