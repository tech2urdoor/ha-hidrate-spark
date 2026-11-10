"""Owns the bottle: when to connect, what to do while connected, and keeping the sips safe."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from bleak.exc import BleakCharacteristicNotFoundError, BleakError
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
    CHARGER_GRACE_SECONDS,
    CHARGER_SYNC_SECONDS,
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


class Unloading(HomeAssistantError):
    """The integration is being reloaded or removed, so nothing more is read, written or saved from here."""


def _missing_characteristic(err: BleakError) -> bool:
    """The bottle has it, so this means the service table was cached incomplete."""
    if isinstance(err, BleakCharacteristicNotFoundError):
        return True
    # A bottle that has gone can also be reported as "not found", and looking again won't bring it back.
    text = str(err).lower()
    return "characteristic" in text and "was not found" in text


def _seconds_of_day(value: str) -> int:
    hours, minutes, *seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + (seconds[0] if seconds else 0)


class HidrateCoordinator:
    """Connects when the bottle is heard and a sync is due. One session at a time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        # Looked up at each sip, so a change of Home Assistant's time zone is followed without a reload.
        self.log = SipLog(lambda: dt_util.DEFAULT_TIME_ZONE)
        self.battery: int | None = None
        # One of protocol.CHARGE_STATES, as of the last sync that could read it.
        self.charge: str | None = None
        self.last_charged: datetime | None = None
        self.firmware: str | None = None
        self.serial: str | None = None
        self.colour: str | None = None
        self.weight: int | None = None
        self.weight_at: datetime | None = None
        self.mark_empty: int | None = None
        self.mark_full: int | None = None
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
        self._closed = False

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

    def water_level(self) -> tuple[float, datetime, str] | None:
        """Share of the bottle, as of when, from where. The reading wins unless a sip came after it."""
        sips = self.log.level()
        share = protocol.marked_share(self.weight, self.mark_empty, self.mark_full)
        if share is not None and self.weight_at and (sips is None or sips.at <= self.weight_at):
            return share, self.weight_at, "reading"
        return (sips.share, sips.at, "sips") if sips else None

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
        self.charge = data.get("charge")
        self.firmware = data.get("firmware")
        self.serial = data.get("serial")
        self.colour = data.get("colour")
        self.weight = data.get("weight")
        self.mark_empty = data.get("mark_empty")
        self.mark_full = data.get("mark_full")
        if stamp := data.get("weight_at"):
            self.weight_at = datetime.fromisoformat(stamp)
        if stamp := data.get("last_charged"):
            self.last_charged = datetime.fromisoformat(stamp)
        if stamp := data.get("last_sync"):
            self.last_sync = datetime.fromisoformat(stamp)

    def _as_dict(self) -> dict[str, Any]:
        return {
            "log": self.log.as_dict(),
            "battery": self.battery,
            "charge": self.charge,
            "firmware": self.firmware,
            "serial": self.serial,
            "colour": self.colour,
            "weight": self.weight,
            "weight_at": self.weight_at.isoformat() if self.weight_at else None,
            "mark_empty": self.mark_empty,
            "mark_full": self.mark_full,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_charged": self.last_charged.isoformat() if self.last_charged else None,
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
        if self.heard_recently():
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
        if self._succeeded and (self.charge in protocol.ON_CHARGER or self._recently_charged()):
            wait = min(wait, CHARGER_SYNC_SECONDS)
        return time.monotonic() - self._last_attempt >= wait

    def _recently_charged(self) -> bool:
        return self.last_charged is not None and (
            dt_util.utcnow() - self.last_charged
        ).total_seconds() < CHARGER_GRACE_SECONDS

    async def async_close(self) -> None:
        """Lets a running session finish, so its sips are saved before the store can be loaded again.

        Whatever was waiting behind that session finds this closed and saves nothing: by then another
        coordinator may have loaded the store, and a save from here would overwrite what it read.
        """
        async with self._lock:
            self._closed = True

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
        """Connects and runs the work, once more if the bottle's service table came back incomplete.

        A connection now and then finds the services without one the bottle really has, and then every later
        connection reuses that cached table and fails the same way. Clearing the cache makes the next one look
        again. The work is safe to repeat: sips are saved as they arrive and a repeat is skipped, and every
        write sets a value rather than adding to one.
        """
        try:
            return await self._session_once(work)
        except BleakError as err:
            if not _missing_characteristic(err):
                raise
            _LOGGER.debug("A characteristic was missing (%s), so trying again with the services looked up afresh", err)
        return await self._session_once(work)

    async def _session_once(self, work: Callable[[Any], Awaitable[Any]]) -> Any:
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
            except BleakError as err:
                if _missing_characteristic(err):
                    try:
                        await client.clear_cache()
                    except CONNECTION_ERRORS as clear_err:
                        _LOGGER.debug("Couldn't clear the service cache: %s", clear_err)
                raise
            finally:
                try:
                    await client.disconnect()
                except CONNECTION_ERRORS as err:
                    # The work has finished by now, and this mustn't replace its result or its error.
                    _LOGGER.debug("Couldn't disconnect cleanly: %s", err)

    async def async_sync(self) -> None:
        """Connect, take the sips, write the reminders. Errors are recorded, never raised."""
        async with self._lock:
            if self._closed:
                return
            self._last_attempt = time.monotonic()
            took_any = False
            keep_raw = False

            def on_firmware(firmware: protocol.Firmware) -> None:
                nonlocal keep_raw
                keep_raw = firmware.keeps_raw_records

            def on_sip(packet: protocol.Packet, received: float) -> None:
                nonlocal took_any
                took_any = self._take(packet, received, keep_raw) or took_any

            async def work(client: Any) -> session.SyncResult:
                return await session.sync(
                    client,
                    size_ml=self.size_ml,
                    on_sip=on_sip,
                    total_today=self.total_today,
                    now_seconds=self._now_seconds,
                    glow=self._glow(),
                    known_serial=self.serial,
                    on_firmware=on_firmware,
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
                if result.charge is not None:
                    self.charge = result.charge
                    if result.charge in protocol.ON_CHARGER:
                        self.last_charged = dt_util.utcnow()
                if result.weight is not None:
                    self.weight = result.weight
                    self.weight_at = dt_util.utcnow()
                self.last_sync = dt_util.utcnow()
                self.last_error = None
                self._succeeded = True
                self._update_device()
                _LOGGER.debug(
                    "Synced: %s sips read, battery %s, %s", result.packets, result.battery, result.charge
                )
            finally:
                self.log.prune(dt_util.utcnow())
                await self._store.async_save(self._as_dict())
            self._notify()
            if took_any:
                _LOGGER.debug("New sips, today now %.0f mL", self.total_today())

    def _take(self, packet: protocol.Packet, received: float, keep_raw: bool) -> bool:
        """Saves one record. False for a refill or a repeat of one already held."""
        at = datetime.fromtimestamp(received - max(0, packet.seconds_ago), timezone.utc)
        if keep_raw:
            self.log.keep_raw(at, packet.data)
            self._store.async_delay_save(self._as_dict, 1)
            _LOGGER.info("Record from a bottle not yet tried with this integration: %s", packet.data.hex())
        if self.log.add(packet, at, self.size_ml) is None:
            _LOGGER.debug("Skipped %s (refill or repeat)", packet)
            return False
        # Reads look one-shot, so the sip gets to disk before anything else can go wrong.
        self._store.async_delay_save(self._as_dict, 1)
        return True

    def heard_recently(self) -> bool:
        """Whether the bottle is in range of Home Assistant, which then reads it without a phone's help."""
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info is not None and time.monotonic() - info.time < HEARD_WITHIN_SECONDS

    async def async_relayed(
        self,
        firmware: protocol.Firmware,
        records: list[tuple[protocol.Packet, float]],
        *,
        connected: bool,
        battery: int | None = None,
        status: bytes | None = None,
    ) -> tuple[int, list[tuple[str, bytes]]]:
        """Takes what a phone read off the bottle. Returns how many sips were new, and what the phone should write.

        Records can arrive long after they were read, from a phone that had no signal at the time, and
        then it isn't connected to the bottle any more: they're saved and nothing else changes.
        """
        if not firmware.supported:
            raise session.UnsupportedBottle(f"Firmware {firmware.raw} isn't a kind this integration can read")
        async with self._lock:
            if self._closed:
                raise Unloading("The integration is reloading, so send these again shortly")
            taken = 0
            try:
                for packet, received in records:
                    if packet.complete and not protocol.is_empty(packet, self.size_ml):
                        taken += self._take(packet, received, firmware.keeps_raw_records)
                if connected:
                    self._last_attempt = time.monotonic()
                    self._unsupported = False
                    self.firmware = firmware.raw
                    if battery is not None:
                        self.battery = battery
                    charge = protocol.charge_state(status) if status else None
                    if charge is not None:
                        self.charge = charge
                        if charge in protocol.ON_CHARGER:
                            self.last_charged = dt_util.utcnow()
                    self.last_sync = dt_util.utcnow()
                    self.last_error = None
                    self._succeeded = True
                    self._update_device()
            finally:
                self.log.prune(dt_util.utcnow())
                await self._store.async_save(self._as_dict())
            self._notify()
            glow = self._glow() if connected else None
            if glow is None:
                return taken, []
            return taken, protocol.reminder_writes(
                glow, firmware, self.size_ml, self.total_today(), self._now_seconds()
            )

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
            if self._closed:
                raise Unloading("The integration is reloading, so try again shortly")
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

    async def async_mark(self, full: bool) -> None:
        """Remembers this reading as empty or full. Stays in Home Assistant, nothing goes to the bottle."""
        # A read the bottle refuses is said as that, rather than as a bottle that wouldn't keep still.
        weight = await self._async_action(lambda client: session.read_weight(client, must=True))
        if weight is None:
            raise HomeAssistantError("Couldn't get a steady reading. Stand the bottle upright and try again")
        if full:
            self.mark_full = weight
        else:
            self.mark_empty = weight
        self.weight = weight
        self.weight_at = dt_util.utcnow()
        await self._store.async_save(self._as_dict())
        self._notify()

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
