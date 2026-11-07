"""Owns the bottle: when to connect, what to do while connected, and keeping the sips safe."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import protocol, session
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
    DUE_CHECK_SECONDS,
    HEARD_WITHIN_SECONDS,
    RETRY_SECONDS,
    SESSION_TIMEOUT_SECONDS,
)
from .sip_log import SipLog

_LOGGER = logging.getLogger(__name__)

CONNECTION_ERRORS = (BleakError, TimeoutError, EOFError, OSError)


def _seconds_of_day(value: str) -> int:
    hours, minutes, *seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + (seconds[0] if seconds else 0)


class HidrateCoordinator:
    """Connects when the bottle is heard and a sync is due. One session at a time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.log = SipLog(dt_util.DEFAULT_TIME_ZONE)
        self.battery: int | None = None
        self.firmware: str | None = None
        self.serial: str | None = None
        self.colour: str | None = None
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{self.address.replace(':', '').lower()}"
        )
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._last_attempt: float | None = None
        self._succeeded = False
        self._unsupported = False

    def option(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULTS[key])

    @property
    def size_ml(self) -> float:
        return float(self.option(CONF_SIZE_ML))

    @property
    def goal_ml(self) -> float:
        return float(self.option(CONF_GOAL_ML))

    @property
    def light(self) -> int:
        return protocol.LIGHTS[self.option(CONF_LIGHT)]

    def _glow(self) -> protocol.GlowSettings | None:
        if not self.option(CONF_MANAGE_REMINDERS):
            return None
        return protocol.GlowSettings(
            goal_ml=self.goal_ml,
            wake_s=_seconds_of_day(self.option(CONF_WAKE)),
            sleep_s=_seconds_of_day(self.option(CONF_SLEEP)),
            frequency_min=int(self.option(CONF_FREQUENCY_MIN)),
            light=self.light,
            always=bool(self.option(CONF_GLOW_ALWAYS)),
            on_sip=bool(self.option(CONF_GLOW_ON_SIP)),
            on_goal=bool(self.option(CONF_GLOW_ON_GOAL)),
        )

    def today(self) -> date:
        return dt_util.now().date()

    def total_today(self) -> float:
        return self.log.total_for(self.today())

    def total_yesterday(self) -> float:
        return self.log.total_for(self.today() - timedelta(days=1))

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.log.load(data.get("log", {}))
        self.battery = data.get("battery")
        self.firmware = data.get("firmware")
        self.serial = data.get("serial")
        self.colour = data.get("colour")
        if stamp := data.get("last_sync"):
            self.last_sync = datetime.fromisoformat(stamp)

    def _as_dict(self) -> dict[str, Any]:
        return {
            "log": self.log.as_dict(),
            "battery": self.battery,
            "firmware": self.firmware,
            "serial": self.serial,
            "colour": self.colour,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
        }

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        cancel_advert = bluetooth.async_register_callback(
            self.hass,
            self._on_advert,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        # Home Assistant drops an advert identical to the last one before any callback runs, and this
        # bottle's never change, so the callback only fires at startup and when it comes back.
        cancel_due_check = async_track_time_interval(
            self.hass, self._on_due_check, timedelta(seconds=DUE_CHECK_SECONDS)
        )
        cancel_midnight = async_track_time_change(
            self.hass, self._on_midnight, hour=0, minute=0, second=1
        )

        def _stop() -> None:
            cancel_advert()
            cancel_due_check()
            cancel_midnight()

        return _stop

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        self._notify()

    @callback
    def _on_advert(
        self,
        _info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        self._sync_if_due()

    @callback
    def _on_due_check(self, _now: datetime) -> None:
        # The last-heard time keeps moving even for adverts that never reach a callback. An entry
        # outlives the bottle's departure by several minutes, so its being there isn't enough.
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        if info is not None and time.monotonic() - info.time < HEARD_WITHIN_SECONDS:
            self._sync_if_due()

    @callback
    def _sync_if_due(self) -> None:
        if self._lock.locked() or not self._due():
            return
        # Set here as well as in async_sync, or an advert arriving before the task starts queues a second.
        self._last_attempt = time.monotonic()
        self.entry.async_create_background_task(
            self.hass, self.async_sync(), f"{DOMAIN} sync {self.address}"
        )

    def _due(self) -> bool:
        if self._last_attempt is None:
            return True
        # Trying again won't change a bottle's firmware, and every try costs it battery.
        settled = self._succeeded or self._unsupported
        wait = int(self.option(CONF_SYNC_MINUTES)) * 60 if settled else RETRY_SECONDS
        return time.monotonic() - self._last_attempt >= wait

    async def async_wait_until_idle(self) -> None:
        """Lets a running session finish, so its sips are saved before the store can be loaded again."""
        async with self._lock:
            pass

    @callback
    def async_set_option(self, key: str, value: Any) -> None:
        """Change one setting. The entry's update listener then calls async_options_changed."""
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )

    @callback
    def async_options_changed(self) -> None:
        """Changed settings go to the bottle the next time it's heard."""
        self._last_attempt = None
        self._notify()

    async def _session(self, work: Callable[[Any], Awaitable[Any]]) -> Any:
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if device is None:
            raise BleakError("Bottle isn't in range of a connectable proxy")
        async with asyncio.timeout(SESSION_TIMEOUT_SECONDS):
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                self.entry.title,
                max_attempts=3,
                ble_device_callback=lambda: (
                    bluetooth.async_ble_device_from_address(
                        self.hass, self.address, connectable=True
                    )
                    or device
                ),
            )
            try:
                return await work(client)
            finally:
                try:
                    await client.disconnect()
                except CONNECTION_ERRORS as err:
                    # The work has finished by now, and this mustn't replace its result or its error.
                    _LOGGER.debug("Couldn't disconnect cleanly: %s", err)

    async def async_sync(self) -> None:
        """Connect, take the sips, write the reminders. Errors are recorded, never raised."""
        async with self._lock:
            self._last_attempt = time.monotonic()
            took_any = False

            def on_sip(packet: protocol.Packet, received: float) -> None:
                nonlocal took_any
                at = datetime.fromtimestamp(received - max(0, packet.seconds_ago), timezone.utc)
                if self.log.add(packet, at, self.size_ml) is None:
                    _LOGGER.debug("Skipped %s (refill or repeat)", packet)
                    return
                took_any = True
                # Reads look one-shot, so the sip gets to disk before anything else can go wrong.
                self._store.async_delay_save(self._as_dict, 1)

            async def work(client: Any) -> session.SyncResult:
                return await session.sync(
                    client,
                    size_ml=self.size_ml,
                    on_sip=on_sip,
                    total_today=self.total_today,
                    now_seconds=self._now_seconds,
                    glow=self._glow(),
                    known_serial=self.serial,
                )

            try:
                result = await self._session(work)
            except session.UnsupportedBottle as err:
                self._unsupported = True
                self._record_failure(err)
            except (*CONNECTION_ERRORS, ValueError) as err:
                self._record_failure(err)
            else:
                self._unsupported = False
                self.firmware = result.firmware.raw
                self.serial = result.serial
                if result.battery is not None:
                    self.battery = result.battery
                self.last_sync = dt_util.utcnow()
                self.last_error = None
                self._succeeded = True
                self._update_device()
                _LOGGER.debug("Synced: %s sips read, battery %s", result.packets, result.battery)
            finally:
                self.log.prune(dt_util.utcnow())
                await self._store.async_save(self._as_dict())
            self._notify()
            if took_any:
                _LOGGER.debug("New sips, today now %.0f mL", self.total_today())

    def _record_failure(self, err: Exception) -> None:
        if self._succeeded or self.last_error is None:
            _LOGGER.warning("Couldn't sync the bottle: %s", err)
        self.last_error = str(err) or type(err).__name__
        self._succeeded = False

    def _now_seconds(self) -> int:
        now = dt_util.now()
        return now.hour * 3600 + now.minute * 60 + now.second

    def _update_device(self) -> None:
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.address)},
            sw_version=self.firmware,
            serial_number=self.serial,
        )

    async def _async_action(self, work: Callable[[Any], Awaitable[Any]]) -> Any:
        """For something the user asked for: they should see why it failed."""
        async with self._lock:
            try:
                return await self._session(work)
            except CONNECTION_ERRORS as err:
                raise HomeAssistantError(f"Couldn't reach the bottle: {err}") from err

    async def async_find(self) -> None:
        await self._async_action(
            lambda client: client.write_gatt_char(
                protocol.GLOW_NOW, protocol.find_command(self.light)
            )
        )

    async def async_set_colour(self, colour: str) -> None:
        """Remembers the colour only once it has read back, so a failure shows as unknown."""
        self.colour = None
        try:
            await self._async_action(lambda client: session.set_colour(client, colour))
        except session.ColourNotSet as err:
            raise HomeAssistantError(str(err)) from err
        else:
            self.colour = colour
        finally:
            await self._store.async_save(self._as_dict())
            self._notify()

    async def async_calibrate_empty(self) -> None:
        async def work(client: Any) -> None:
            firmware = await session.read_firmware(client)
            await client.write_gatt_char(
                protocol.COMMANDS, protocol.calibrate_empty_command(firmware)
            )

        await self._async_action(work)

    async def async_calibrate_full(self) -> None:
        await self._async_action(
            lambda client: client.write_gatt_char(
                protocol.COMMANDS, protocol.calibrate_full_command()
            )
        )

    async def async_probe(self, weight_seconds: float = 0) -> dict[str, Any]:
        """A read-only look at the bottle, safe to paste in public. Takes no sips and writes nothing."""
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        texts = [self.serial]
        if info is not None:
            scanner = bluetooth.async_scanner_by_source(self.hass, info.source)
            # A scanner's name is "<proxy name> (<address>)", and connection errors quote the proxy name.
            texts += [info.name, info.source, scanner.name.partition(" (")[0] if scanner else None]
        private = session.private_values(*texts, address=self.address)
        try:
            report = await self._async_action(lambda client: session.probe(client, private, weight_seconds))
        except HomeAssistantError as err:
            raise HomeAssistantError(session.redact_text(str(err), private)) from None
        if info is not None:
            report["heard"] = {"rssi": info.rssi, "name": session.mask_name(info.name)}
        return report
