"""Finding the bottle, and the settings the reminders need."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

from . import bermuda
from .const import (
    CONF_FREQUENCY_MIN,
    CONF_GLOW_ALWAYS,
    CONF_GLOW_ON_GOAL,
    CONF_GLOW_ON_SIP,
    CONF_GOAL_ML,
    CONF_LIGHT,
    CONF_MANAGE_REMINDERS,
    CONF_SIZE_ML,
    CONF_SLEEP,
    CONF_SYNC_MINUTES,
    CONF_WAKE,
    DEFAULTS,
    DOMAIN,
    FREQUENCY_RANGE,
    GOAL_RANGE,
)
from .protocol import LIGHTS, SERVICE

TITLE = "Water Bottle"
CONF_BERMUDA = "bermuda"


def _number(low: int, high: int, step: int, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low, max=high, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


def _size_field() -> dict[Any, Any]:
    """Asked at setup because the first sync can read sips, and their amounts are worked out from this."""
    return {vol.Required(CONF_SIZE_ML, default=DEFAULTS[CONF_SIZE_ML]): _number(100, 3000, 1, "mL")}


class HidrateConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._size_ml: float | None = None

    def _setup_fields(self) -> dict[Any, Any]:
        """The size, and the Bermuda tick box only where Bermuda is running."""
        fields = _size_field()
        if bermuda.available(self.hass):
            fields[vol.Required(CONF_BERMUDA, default=False)] = BooleanSelector()
        return fields

    async def _async_finish(self, address: str, user_input: dict[str, Any]) -> ConfigFlowResult:
        self._address = address
        self._size_ml = user_input[CONF_SIZE_ML]
        if user_input.get(CONF_BERMUDA) and not await bermuda.async_track(self.hass, address):
            return await self.async_step_bermuda_manual()
        return self._create()

    def _create(self) -> ConfigFlowResult:
        assert self._address is not None
        return self.async_create_entry(
            title=TITLE,
            data={CONF_ADDRESS: self._address},
            options={CONF_SIZE_ML: self._size_ml},
        )

    async def async_step_bermuda_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Bermuda couldn't be changed from here, so say how, and set the bottle up anyway."""
        if user_input is not None:
            return self._create()
        return self.async_show_form(
            step_id="bermuda_manual",
            data_schema=vol.Schema({}),
            description_placeholders={"address": (self._address or "").upper()},
        )

    @staticmethod
    @callback
    def async_get_options_flow(_entry: ConfigEntry) -> OptionsFlow:
        return HidrateOptionsFlow()

    async def async_step_bluetooth(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(info.address)
        self._abort_if_unique_id_configured()
        self._discovery = info
        self.context["title_placeholders"] = {"name": info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovery is not None
        if user_input is not None:
            return await self._async_finish(self._discovery.address, user_input)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(self._setup_fields()),
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_ADDRESS])
            self._abort_if_unique_id_configured()
            return await self._async_finish(user_input[CONF_ADDRESS], user_input)
        current = self._async_current_ids()
        found = {
            info.address: f"{info.name} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if SERVICE in info.service_uuids
            and info.address not in current
            and (info.name or "").startswith("h2o")
        }
        if not found:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(found), **self._setup_fields()}),
        )


class HidrateOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        def default(key: str) -> Any:
            return self.config_entry.options.get(key, DEFAULTS[key])

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MANAGE_REMINDERS, default=default(CONF_MANAGE_REMINDERS)): BooleanSelector(),
                    vol.Required(CONF_GOAL_ML, default=default(CONF_GOAL_ML)): _number(*GOAL_RANGE, "mL"),
                    vol.Required(CONF_WAKE, default=default(CONF_WAKE)): TimeSelector(),
                    vol.Required(CONF_SLEEP, default=default(CONF_SLEEP)): TimeSelector(),
                    vol.Required(CONF_FREQUENCY_MIN, default=default(CONF_FREQUENCY_MIN)): _number(*FREQUENCY_RANGE, "min"),
                    vol.Required(CONF_LIGHT, default=default(CONF_LIGHT)): SelectSelector(
                        SelectSelectorConfig(
                            options=list(LIGHTS),
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key="glow_light",
                        )
                    ),
                    vol.Required(CONF_GLOW_ALWAYS, default=default(CONF_GLOW_ALWAYS)): BooleanSelector(),
                    vol.Required(CONF_GLOW_ON_SIP, default=default(CONF_GLOW_ON_SIP)): BooleanSelector(),
                    vol.Required(CONF_GLOW_ON_GOAL, default=default(CONF_GLOW_ON_GOAL)): BooleanSelector(),
                    vol.Required(CONF_SIZE_ML, default=default(CONF_SIZE_ML)): _number(100, 3000, 1, "mL"),
                    vol.Required(CONF_SYNC_MINUTES, default=default(CONF_SYNC_MINUTES)): _number(5, 720, 5, "min"),
                }
            ),
        )
