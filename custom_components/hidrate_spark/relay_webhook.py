"""The address a phone calls to hand over what it read off the bottle. One per bottle, and only while the option is on."""
from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from . import relay, session
from .const import CONF_PHONE_RELAY, CONF_WEBHOOK_ID, DOMAIN
from .coordinator import Unloading

_LOGGER = logging.getLogger(__name__)


@callback
def async_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Makes the webhook match the option. Safe to call whenever either might have changed."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if not webhook_id:
        return
    webhook.async_unregister(hass, webhook_id)
    if entry.options.get(CONF_PHONE_RELAY):
        webhook.async_register(
            hass,
            DOMAIN,
            f"{entry.title} phone relay",
            webhook_id,
            partial(_async_handle, entry),
            allowed_methods=("POST",),
        )


@callback
def async_remove(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if webhook_id := entry.data.get(CONF_WEBHOOK_ID):
        webhook.async_unregister(hass, webhook_id)


def address_for(hass: HomeAssistant, webhook_id: str) -> str:
    """The full address if Home Assistant knows its own, or else the path to put after it."""
    try:
        return webhook.async_generate_url(hass, webhook_id)
    except Exception:  # noqa: BLE001 - no address is set, so the path is the most that can be shown
        return webhook.async_generate_path(webhook_id)


def _refused(reason: str, record: int | None = None) -> web.Response:
    body: dict[str, Any] = {"error": reason}
    if record is not None:
        body["record"] = record
    return web.json_response(body, status=400)


async def _async_handle(
    entry: ConfigEntry, _hass: HomeAssistant, _webhook_id: str, request: web.Request
) -> web.Response:
    try:
        body: Any = await request.json()
    except ValueError:
        return _refused("The body isn't JSON")
    if not isinstance(body, dict):
        return _refused("The body isn't an object")
    coordinator = entry.runtime_data
    step = body.get("step")
    try:
        if step == "check":
            # In range of Home Assistant, the bottle is read from here and the phone can save its battery.
            if coordinator.heard_recently():
                return web.json_response({"go": False, "reason": "in_range"})
            return web.json_response({"go": True})
        if step == "begin":
            return web.json_response(relay.instructions(relay.firmware_from(body)))
        if step == "sips":
            records = relay.records_from(body, time.time())
            saved, writes = await coordinator.async_relayed(
                relay.firmware_from(body),
                records,
                connected=body.get("connected") is True,
                battery=relay.battery_from(body),
                status=relay.status_from(body),
            )
            _LOGGER.debug("A phone handed over %s records, %s of them new sips", len(records), saved)
            return web.json_response(
                {"received": len(records), "saved": saved, "writes": relay.writes_reply(writes)}
            )
    except relay.BadRequest as err:
        return _refused(str(err), err.record)
    except session.UnsupportedBottle as err:
        return _refused(str(err))
    except Unloading as err:
        # Not a refusal: the phone keeps its records and brings them back on its next run.
        return web.json_response({"error": str(err)}, status=503)
    return _refused("Unknown step")
