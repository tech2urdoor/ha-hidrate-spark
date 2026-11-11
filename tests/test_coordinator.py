"""When the coordinator decides to sync. Needs a real Home Assistant installed, as tests/check_imports.py does.

    python tests/test_coordinator.py
"""

import asyncio
import json
import struct
import sys
import time
import unittest
from datetime import time as dt_time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import custom_components.hidrate_spark as integration  # noqa: E402
from custom_components.hidrate_spark import binary_sensor as binary_sensor_platform  # noqa: E402
from custom_components.hidrate_spark import config_flow  # noqa: E402
from custom_components.hidrate_spark import coordinator as module  # noqa: E402
from custom_components.hidrate_spark import number as number_platform  # noqa: E402
from custom_components.hidrate_spark import relay_webhook  # noqa: E402
from custom_components.hidrate_spark import select as select_platform  # noqa: E402
from custom_components.hidrate_spark import sensor as sensor_platform  # noqa: E402
from custom_components.hidrate_spark import switch as switch_platform  # noqa: E402
from custom_components.hidrate_spark import time as time_platform  # noqa: E402
from custom_components.hidrate_spark.const import (  # noqa: E402
    CHARGER_GRACE_SECONDS,
    CHARGER_SYNC_SECONDS,
    DEFAULTS,
    DUE_CHECK_SECONDS,
    HEARD_WITHIN_SECONDS,
    RETRY_SECONDS,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
THIRTY_MINUTES = 30 * 60


class Base(unittest.TestCase):
    def setUp(self):
        self.entry = SimpleNamespace(data={"address": ADDRESS}, options={}, title="Test bottle")
        # A sync is a coroutine that nothing here runs, so close it rather than leave it unawaited.
        self.entry.async_create_background_task = mock.Mock(side_effect=lambda _hass, coro, _name: coro.close())
        with mock.patch.object(module, "Store"):
            self.coordinator = module.HidrateCoordinator(mock.Mock(), self.entry)

    @property
    def syncs_started(self):
        return self.entry.async_create_background_task.call_count

    def heard(self, seconds_ago):
        info = None if seconds_ago is None else SimpleNamespace(time=time.monotonic() - seconds_ago)
        patcher = mock.patch.object(module.bluetooth, "async_last_service_info", return_value=info)
        patcher.start()
        self.addCleanup(patcher.stop)

    def last_attempt(self, seconds_ago, succeeded=True):
        self.coordinator._last_attempt = time.monotonic() - seconds_ago
        self.coordinator._succeeded = succeeded


class DueCheckTest(Base):
    def test_syncs_when_due_and_recently_heard(self):
        self.heard(2)
        self.last_attempt(THIRTY_MINUTES + 1)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)

    def test_first_sync_needs_no_earlier_attempt(self):
        self.heard(2)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)

    def test_not_due_yet(self):
        self.heard(2)
        self.last_attempt(THIRTY_MINUTES - 60)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 0)

    def test_a_failure_retries_sooner(self):
        self.heard(2)
        self.last_attempt(RETRY_SECONDS + 1, succeeded=False)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)

    def test_gone_bottle_is_left_alone_while_its_entry_lingers(self):
        self.heard(HEARD_WITHIN_SECONDS + 1)
        self.last_attempt(THIRTY_MINUTES + 1)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 0)

    def test_never_heard(self):
        self.heard(None)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 0)

    def test_nothing_starts_while_connected(self):
        self.heard(2)
        self.last_attempt(THIRTY_MINUTES + 1)

        async def run():
            async with self.coordinator._lock:
                self.coordinator._on_due_check(None)

        asyncio.run(run())
        self.assertEqual(self.syncs_started, 0)

    def test_second_check_before_the_task_runs_starts_nothing(self):
        self.heard(2)
        self.last_attempt(THIRTY_MINUTES + 1)
        self.coordinator._on_due_check(None)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)

    def test_changed_settings_go_out_on_the_next_check(self):
        self.heard(2)
        self.last_attempt(10)
        self.coordinator.async_options_changed()
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)


class AdvertTest(Base):
    def test_advert_still_starts_a_due_sync(self):
        self.last_attempt(THIRTY_MINUTES + 1)
        self.coordinator._on_advert(None, None)
        self.assertEqual(self.syncs_started, 1)

    def test_advert_and_check_together_start_one(self):
        self.heard(2)
        self.last_attempt(THIRTY_MINUTES + 1)
        self.coordinator._on_advert(None, None)
        self.coordinator._on_due_check(None)
        self.assertEqual(self.syncs_started, 1)


class SessionTest(Base):
    def run_session(self, work, disconnect_error=None):
        client = mock.Mock(disconnect=mock.AsyncMock(side_effect=disconnect_error))
        with (
            mock.patch.object(module.bluetooth, "async_ble_device_from_address"),
            mock.patch.object(module, "establish_connection", mock.AsyncMock(return_value=client)),
        ):
            try:
                return asyncio.run(self.coordinator._session(work))
            finally:
                client.disconnect.assert_awaited_once_with()

    def test_a_failed_disconnect_keeps_the_result(self):
        async def work(_client):
            return "result"

        self.assertEqual(self.run_session(work, module.BleakError("gone")), "result")

    def test_a_failed_disconnect_keeps_the_real_error(self):
        async def work(_client):
            raise ValueError("the real problem")

        with self.assertRaisesRegex(ValueError, "the real problem"):
            self.run_session(work, module.BleakError("gone"))


class StaleServicesTest(Base):
    """A cached service table missing a characteristic is cleared, and the work runs once more on a new connection."""

    COMMANDS = "e3578b0d-caa7-46d6-b7c2-7331c08de044"

    def run_with(self, outcomes, clear_error=None):
        clients = []

        def connect(*_args, **_kwargs):
            client = mock.Mock(disconnect=mock.AsyncMock(), clear_cache=mock.AsyncMock(side_effect=clear_error))
            clients.append(client)
            return client

        calls = iter(outcomes)

        async def work(_client):
            outcome = next(calls)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with (
            mock.patch.object(module.bluetooth, "async_ble_device_from_address"),
            mock.patch.object(module, "establish_connection", mock.AsyncMock(side_effect=connect)),
        ):
            try:
                return asyncio.run(self.coordinator._session(work)), clients
            except Exception as err:  # noqa: BLE001
                return err, clients

    def test_a_bottle_that_has_gone_isnt_taken_for_a_missing_characteristic(self):
        gone = module.BleakError("Device with address AA:BB:CC:DD:EE:FF was not found")
        result, clients = self.run_with([gone, "synced"])
        self.assertIs(result, gone)
        self.assertEqual(len(clients), 1)
        clients[0].clear_cache.assert_not_called()

    def test_cleared_and_tried_again(self):
        from bleak.exc import BleakCharacteristicNotFoundError

        result, clients = self.run_with([BleakCharacteristicNotFoundError(self.COMMANDS), "synced"])
        self.assertEqual(result, "synced")
        self.assertEqual(len(clients), 2)
        clients[0].clear_cache.assert_awaited_once_with()
        clients[1].clear_cache.assert_not_awaited()
        for client in clients:
            client.disconnect.assert_awaited_once_with()

    def test_the_plain_message_counts_too(self):
        result, clients = self.run_with([module.BleakError(f"Characteristic {self.COMMANDS} was not found!"), "synced"])
        self.assertEqual(result, "synced")
        clients[0].clear_cache.assert_awaited_once_with()

    def test_only_once(self):
        from bleak.exc import BleakCharacteristicNotFoundError

        missing = BleakCharacteristicNotFoundError(self.COMMANDS)
        result, clients = self.run_with([missing, missing])
        self.assertIs(result, missing)
        self.assertEqual(len(clients), 2)

    def test_other_errors_arent_retried(self):
        result, clients = self.run_with([module.BleakError("out of range")])
        self.assertEqual(str(result), "out of range")
        self.assertEqual(len(clients), 1)
        clients[0].clear_cache.assert_not_awaited()

    def test_a_failed_clear_still_tries_again(self):
        from bleak.exc import BleakCharacteristicNotFoundError

        result, clients = self.run_with(
            [BleakCharacteristicNotFoundError(self.COMMANDS), "synced"], clear_error=module.BleakError("no cache")
        )
        self.assertEqual(result, "synced")
        self.assertEqual(len(clients), 2)


class UnsupportedTest(Base):
    def setUp(self):
        super().setUp()
        self.coordinator._store.async_save = mock.AsyncMock()

    def sync_failing_with(self, err):
        with mock.patch.object(self.coordinator, "_session", mock.AsyncMock(side_effect=err)):
            asyncio.run(self.coordinator.async_sync())

    def test_an_unsupported_bottle_waits_for_the_normal_interval(self):
        self.sync_failing_with(module.session.UnsupportedBottle("no"))
        self.assertEqual(self.coordinator.last_error, "no")
        self.coordinator._last_attempt = time.monotonic() - RETRY_SECONDS - 1
        self.assertFalse(self.coordinator._due())
        self.coordinator._last_attempt = time.monotonic() - THIRTY_MINUTES - 1
        self.assertTrue(self.coordinator._due())

    def test_a_connection_failure_still_retries_sooner(self):
        self.sync_failing_with(module.BleakError("no"))
        self.coordinator._last_attempt = time.monotonic() - RETRY_SECONDS - 1
        self.assertTrue(self.coordinator._due())


class ChargingTest(Base):
    def setUp(self):
        super().setUp()
        self.coordinator._store.async_save = mock.AsyncMock()
        self.coordinator._update_device = mock.Mock()

    def sync_reading(self, charge):
        result = module.session.SyncResult(
            module.protocol.Firmware.parse(b"90.11"), "S", 100, 0, None, charge
        )
        with mock.patch.object(self.coordinator, "_session", mock.AsyncMock(return_value=result)):
            asyncio.run(self.coordinator.async_sync())

    def entities(self):
        binary = binary_sensor_platform.HidrateChargingSensor(self.coordinator, binary_sensor_platform.CHARGING)
        description = next(s for s in sensor_platform.SENSORS if s.key == "battery_state")
        return binary, sensor_platform.HidrateSensor(self.coordinator, description)

    def test_unknown_until_a_sync_reads_it(self):
        binary, state = self.entities()
        self.assertIsNone(binary.is_on)
        self.assertIsNone(state.native_value)

    def test_each_state_reaches_both_entities(self):
        binary, state = self.entities()
        for charge, on in (("charging", True), ("charged", False), ("not_charging", False)):
            self.sync_reading(charge)
            self.assertEqual((binary.is_on, state.native_value), (on, charge))
            self.assertEqual(self.coordinator._as_dict()["charge"], charge)

    def test_every_state_is_an_option_of_the_sensor(self):
        _, state = self.entities()
        self.assertEqual(state.options, list(module.protocol.CHARGE_STATES))

    def test_an_unreadable_status_keeps_the_last_one(self):
        self.sync_reading("charging")
        self.sync_reading(None)
        self.assertEqual(self.coordinator.charge, "charging")

    def test_it_survives_a_restart(self):
        self.sync_reading("charged")
        saved = self.coordinator._as_dict()
        with mock.patch.object(module, "Store"):
            again = module.HidrateCoordinator(mock.Mock(), self.entry)
        again._store.async_load = mock.AsyncMock(return_value=saved)
        asyncio.run(again.async_load())
        self.assertEqual(again.charge, "charged")
        self.assertEqual(again.last_charged, self.coordinator.last_charged)

    def test_on_the_charger_it_syncs_sooner(self):
        for charge in ("charging", "charged"):
            self.coordinator.charge = charge
            self.last_attempt(CHARGER_SYNC_SECONDS + 1)
            self.assertTrue(self.coordinator._due(), charge)
            self.last_attempt(CHARGER_SYNC_SECONDS - 10)
            self.assertFalse(self.coordinator._due(), charge)

    def test_off_the_charger_it_waits_the_normal_interval(self):
        for charge in ("not_charging", None):
            self.coordinator.charge = charge
            self.last_attempt(CHARGER_SYNC_SECONDS + 1)
            self.assertFalse(self.coordinator._due(), charge)
            self.last_attempt(THIRTY_MINUTES + 1)
            self.assertTrue(self.coordinator._due(), charge)

    def test_a_shorter_setting_than_the_charger_check_is_kept(self):
        self.entry.options = {"sync_minutes": 2}
        self.coordinator.charge = "charging"
        self.last_attempt(2 * 60 + 1)
        self.assertTrue(self.coordinator._due())

    def last_charged_entity(self):
        description = next(s for s in sensor_platform.SENSORS if s.key == "last_charged")
        return sensor_platform.HidrateSensor(self.coordinator, description)

    def test_last_charged_is_the_last_sync_on_the_charger(self):
        entity = self.last_charged_entity()
        self.assertIsNone(entity.native_value)
        self.sync_reading("not_charging")
        self.assertIsNone(entity.native_value)
        for charge in ("charging", "charged"):
            self.coordinator.last_charged = None
            self.sync_reading(charge)
            self.assertIsNotNone(entity.native_value, charge)
        on_charger = entity.native_value
        self.sync_reading("not_charging")
        self.sync_reading(None)
        self.assertEqual(entity.native_value, on_charger)

    def test_a_pause_on_the_charger_keeps_the_short_interval(self):
        # A full bottle on its charger stops charging for a minute and starts again; a sync in that pause reads not charging.
        self.sync_reading("charging")
        self.sync_reading("not_charging")
        self.last_attempt(CHARGER_SYNC_SECONDS + 1)
        self.assertTrue(self.coordinator._due())

    def test_after_the_grace_period_it_waits_the_normal_interval(self):
        self.sync_reading("charging")
        self.sync_reading("not_charging")
        self.coordinator.last_charged -= timedelta(seconds=CHARGER_GRACE_SECONDS + 1)
        self.last_attempt(CHARGER_SYNC_SECONDS + 1)
        self.assertFalse(self.coordinator._due())


class UnloadTest(Base):
    def unload(self, unloaded):
        hass = mock.Mock()
        hass.config_entries.async_unload_platforms = mock.AsyncMock(return_value=unloaded)
        self.entry.runtime_data = self.coordinator
        return integration.async_unload_entry(hass, self.entry)

    def test_waits_for_a_running_session(self):
        async def run():
            await self.coordinator._lock.acquire()
            task = asyncio.create_task(self.unload(True))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.coordinator._lock.release()
            self.assertTrue(await task)

        asyncio.run(run())

    def test_a_failed_unload_doesnt_wait(self):
        async def run():
            async with self.coordinator._lock:
                self.assertFalse(await self.unload(False))

        asyncio.run(run())
        self.assertFalse(self.coordinator._closed)

    def test_what_was_waiting_behind_the_session_is_turned_away_and_saves_nothing(self):
        self.coordinator._store.async_save = mock.AsyncMock()
        firmware = module.protocol.Firmware.parse(b"90.11")
        records = [(module.protocol.parse_packet(record()), time.time())]

        async def run():
            await self.coordinator._lock.acquire()
            unload = asyncio.create_task(self.unload(True))
            await asyncio.sleep(0)
            # Queued behind the unload, as a phone's request or a pressed button can be.
            relayed = asyncio.create_task(self.coordinator.async_relayed(firmware, records, connected=True))
            pressed = asyncio.create_task(self.coordinator.async_find())
            sync = asyncio.create_task(self.coordinator.async_sync())
            await asyncio.sleep(0)
            self.coordinator._lock.release()
            self.assertTrue(await unload)
            with self.assertRaises(module.Unloading):
                await relayed
            with self.assertRaises(module.Unloading):
                await pressed
            await sync

        with mock.patch.object(self.coordinator, "_session", mock.AsyncMock()) as session:
            asyncio.run(run())
        session.assert_not_called()
        self.coordinator._store.async_save.assert_not_called()
        self.assertEqual(self.coordinator.log.sips, [])


class ColourTest(Base):
    def setUp(self):
        super().setUp()
        self.coordinator._store.async_save = mock.AsyncMock()

    def set_colour(self, side_effect=None):
        async def session(work):
            return await work(None)

        writer = mock.AsyncMock(side_effect=side_effect)
        with mock.patch.object(module.session, "set_colour", writer):
            with mock.patch.object(self.coordinator, "_session", session):
                try:
                    asyncio.run(self.coordinator.async_set_colour("Blue"))
                finally:
                    writer.assert_awaited_once_with(None, "Blue")

    def test_remembered_once_it_has_read_back(self):
        self.set_colour()
        self.assertEqual(self.coordinator.colour, "Blue")
        self.assertEqual(self.coordinator._as_dict()["colour"], "Blue")
        self.coordinator._store.async_save.assert_awaited_once()

    def test_a_failed_write_shows_as_unknown_and_tells_the_user(self):
        self.coordinator.colour = "Rainbow"
        with self.assertRaises(module.HomeAssistantError):
            self.set_colour(module.session.ColourNotSet("no"))
        self.assertIsNone(self.coordinator.colour)
        self.coordinator._store.async_save.assert_awaited_once()


class ControlsTest(Base):
    def setUp(self):
        super().setUp()
        self.entry.options = {"manage_reminders": True, "size_ml": 700}

        def update(_entry, options):
            self.entry.options = options

        self.coordinator.hass.config_entries.async_update_entry = mock.Mock(side_effect=update)

    def test_a_change_keeps_the_other_settings(self):
        self.coordinator.async_set_option("goal_ml", 2500)
        self.assertEqual(self.entry.options, {"manage_reminders": True, "size_ml": 700, "goal_ml": 2500})

    def test_times_reach_the_schedule(self):
        wake, bedtime = (time_platform.HidrateTime(self.coordinator, d) for d in time_platform.TIMES)
        self.assertEqual(wake.native_value, dt_time(7, 0))
        asyncio.run(wake.async_set_value(dt_time(5, 30, 0, 999)))
        asyncio.run(bedtime.async_set_value(dt_time(21, 15)))
        self.assertEqual(self.entry.options["wake_time"], "05:30:00")
        self.assertEqual(wake.native_value, dt_time(5, 30))
        glow = self.coordinator._glow()
        self.assertEqual((glow.wake_s, glow.sleep_s), (5 * 3600 + 30 * 60, 21 * 3600 + 15 * 60))

    def test_a_time_saved_without_seconds_still_reads(self):
        self.entry.options["wake_time"] = "06:45"
        wake = time_platform.HidrateTime(self.coordinator, time_platform.TIMES[0])
        self.assertEqual(wake.native_value, dt_time(6, 45))

    def test_numbers_reach_the_schedule(self):
        interval, goal = (number_platform.HidrateNumber(self.coordinator, d) for d in number_platform.NUMBERS)
        self.assertEqual((interval.native_value, goal.native_value), (60.0, 2000.0))
        asyncio.run(interval.async_set_native_value(90.0))
        asyncio.run(goal.async_set_native_value(2200.0))
        glow = self.coordinator._glow()
        self.assertEqual((glow.frequency_min, glow.goal_ml), (90, 2200.0))

    def test_switches_reach_the_schedule(self):
        reminders, always = (switch_platform.HidrateSwitch(self.coordinator, d) for d in switch_platform.SWITCHES)
        self.assertTrue(reminders.is_on)
        self.assertFalse(always.is_on)
        asyncio.run(always.async_turn_on())
        self.assertTrue(self.coordinator._glow().always)
        asyncio.run(reminders.async_turn_off())
        self.assertFalse(reminders.is_on)
        self.assertIsNone(self.coordinator._glow())

    def test_glow_style_reaches_the_schedule(self):
        style = select_platform.HidrateGlowStyleSelect(self.coordinator, select_platform.GLOW_STYLE)
        self.assertEqual(style.current_option, "high")
        asyncio.run(style.async_select_option("low"))
        self.assertEqual(self.coordinator._glow().light, module.protocol.LIGHTS["low"])

    def test_every_control_has_a_default(self):
        descriptions = (*time_platform.TIMES, *number_platform.NUMBERS, *switch_platform.SWITCHES)
        for description in (*descriptions, select_platform.GLOW_STYLE):
            self.assertIn(description.key, DEFAULTS)


class StartTest(Base):
    def test_the_check_is_scheduled_and_every_listener_is_cancelled(self):
        cancels = [mock.Mock(), mock.Mock(), mock.Mock()]
        with (
            mock.patch.object(module.bluetooth, "async_register_callback", return_value=cancels[0]),
            mock.patch.object(module, "async_track_time_interval", return_value=cancels[1]) as interval,
            mock.patch.object(module, "async_track_time_change", return_value=cancels[2]),
        ):
            stop = self.coordinator.async_start()
        interval.assert_called_once_with(
            self.coordinator.hass, self.coordinator._on_due_check, timedelta(seconds=DUE_CHECK_SECONDS)
        )
        stop()
        for cancel in cancels:
            cancel.assert_called_once_with()


class LevelSensorsTest(Base):
    @staticmethod
    def sensor(key):
        return next(s for s in sensor_platform.SENSORS if s.key == key)

    def add(self, minutes, start, stop):
        packet = module.protocol.Packet(0, True, 1, 0, 100, 500, start, stop)
        at = datetime(2026, 11, 7, 10, minutes, tzinfo=timezone.utc)
        self.coordinator.log.add(packet, at, self.coordinator.size_ml)
        return at

    def test_unknown_until_there_is_a_sip(self):
        for key in ("water_left", "last_refill"):
            self.assertIsNone(self.sensor(key).value_fn(self.coordinator))
            self.assertTrue(all(v is None for v in self.sensor(key).attrs_fn(self.coordinator).values()))

    def test_water_left_follows_the_size_setting(self):
        at = self.add(0, 400, 300)
        self.entry.options["size_ml"] = 1000
        self.assertEqual(self.sensor("water_left").value_fn(self.coordinator), 500)
        self.assertEqual(
            self.sensor("water_left").attrs_fn(self.coordinator),
            {"percent": 50, "as_of": at.isoformat(), "source": "sips", "reading": None},
        )

    def marked(self, minutes, weight):
        at = datetime(2026, 11, 7, 10, minutes, tzinfo=timezone.utc)
        self.coordinator.mark_empty, self.coordinator.mark_full = 1000, 2000
        self.coordinator.weight, self.coordinator.weight_at = weight, at
        self.entry.options["size_ml"] = 500
        return at

    def test_water_left_comes_from_the_reading_once_marked(self):
        self.add(0, 400, 300)
        at = self.marked(5, 1500)
        self.assertEqual(self.sensor("water_left").value_fn(self.coordinator), 250)
        self.assertEqual(
            self.sensor("water_left").attrs_fn(self.coordinator),
            {"percent": 50, "as_of": at.isoformat(), "source": "reading", "reading": 1500},
        )

    def test_a_sip_after_the_reading_wins(self):
        self.marked(5, 1500)
        self.add(10, 400, 300)
        self.assertEqual(self.sensor("water_left").attrs_fn(self.coordinator)["source"], "sips")

    def test_a_reading_that_cannot_be_water_falls_back_to_sips(self):
        self.add(0, 400, 300)
        self.marked(5, 500)
        self.assertEqual(self.sensor("water_left").attrs_fn(self.coordinator)["source"], "sips")

    def test_without_both_marks_it_stays_on_sips(self):
        self.add(0, 400, 300)
        self.marked(5, 1500)
        self.coordinator.mark_full = None
        self.assertEqual(self.sensor("water_left").attrs_fn(self.coordinator)["source"], "sips")

    def test_marks_are_remembered_and_nothing_is_written(self):
        client = mock.Mock(read_gatt_char=mock.AsyncMock(return_value=bytes.fromhex("8c05")))
        client.write_gatt_char = mock.AsyncMock()
        self.coordinator._store = mock.Mock(async_save=mock.AsyncMock())

        async def session(work):
            return await work(client)

        with mock.patch.object(self.coordinator, "_session", session):
            asyncio.run(self.coordinator.async_mark(full=True))
        self.assertEqual(self.coordinator.mark_full, 35845)
        self.assertEqual(self.coordinator._as_dict()["mark_full"], 35845)
        client.write_gatt_char.assert_not_called()

    def test_a_mark_the_bottle_refuses_to_be_read_for_says_so(self):
        self.coordinator.mark_full = 2000
        client = mock.Mock(read_gatt_char=mock.AsyncMock(side_effect=module.BleakError("Read not permitted")))

        async def session(work):
            return await work(client)

        with mock.patch.object(self.coordinator, "_session", session):
            with self.assertRaisesRegex(module.HomeAssistantError, "Read not permitted"):
                asyncio.run(self.coordinator.async_mark(full=True))
        self.assertEqual(self.coordinator.mark_full, 2000)

    def test_an_unsteady_mark_tells_the_user_and_keeps_the_old_one(self):
        self.coordinator.mark_full = 2000

        async def session(_work):
            return None

        with mock.patch.object(self.coordinator, "_session", session):
            with self.assertRaises(module.HomeAssistantError):
                asyncio.run(self.coordinator.async_mark(full=True))
        self.assertEqual(self.coordinator.mark_full, 2000)

    def test_last_refill_says_when_and_how_much(self):
        self.add(0, 300, 200)
        at = self.add(10, 400, 300)
        self.entry.options["size_ml"] = 1000
        self.assertEqual(self.sensor("last_refill").value_fn(self.coordinator), at)
        self.assertEqual(
            self.sensor("last_refill").attrs_fn(self.coordinator),
            {"amount_ml": 500, "exact_time": False},
        )


class SetupTest(unittest.TestCase):
    """A bottle that has never met the app is set up from here, so the size has to be asked for."""

    def flow(self):
        flow = config_flow.HidrateConfigFlow()
        flow.hass = mock.Mock()
        flow.hass.config_entries.async_entries.return_value = []  # no Bermuda
        flow.context = {}
        flow._discovery = SimpleNamespace(address=ADDRESS, name="h2oTEST")
        return flow

    def test_discovery_asks_for_the_size_before_adding(self):
        result = asyncio.run(self.flow().async_step_confirm())
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["data_schema"]({})["size_ml"], DEFAULTS["size_ml"])

    def test_the_size_chosen_is_what_the_first_sync_uses(self):
        result = asyncio.run(self.flow().async_step_confirm({"size_ml": 946}))
        self.assertEqual(result["data"], {"address": ADDRESS})
        self.assertEqual(result["options"], {"size_ml": 946})

    def test_choosing_by_hand_keeps_the_size_out_of_the_entry_data(self):
        flow = self.flow()
        flow.async_set_unique_id = mock.AsyncMock()
        flow._abort_if_unique_id_configured = mock.Mock()
        result = asyncio.run(flow.async_step_user({"address": ADDRESS, "size_ml": 946}))
        self.assertEqual(result["data"], {"address": ADDRESS})
        self.assertEqual(result["options"], {"size_ml": 946})


class BermudaSetupTest(unittest.TestCase):
    """Setup can add the bottle to Bermuda, through Bermuda's own settings, and says how if it can't."""

    def bermuda_entry(self, picked=(), state=None):
        from homeassistant.config_entries import ConfigEntryState

        return SimpleNamespace(
            entry_id="b1", options={"configured_devices": list(picked)}, state=state or ConfigEntryState.LOADED
        )

    def hass(self, entries, answers=()):
        hass = mock.Mock()
        hass.config_entries.async_entries.return_value = entries
        flows = hass.config_entries.options
        flows.async_init = mock.AsyncMock(
            return_value={"type": "menu", "flow_id": "f1", "menu_options": ["globalopts", "selectdevices"]}
        )
        flows.async_configure = mock.AsyncMock(side_effect=list(answers))
        flows.async_abort = mock.Mock()
        return hass

    def flow(self, hass):
        flow = config_flow.HidrateConfigFlow()
        flow.hass = hass
        flow.context = {}
        flow._discovery = SimpleNamespace(address=ADDRESS.lower(), name="h2oTEST")
        return flow

    FORM = {"type": "form", "step_id": "selectdevices"}
    DONE = {"type": "create_entry"}

    def test_no_tick_box_without_bermuda(self):
        result = asyncio.run(self.flow(self.hass([])).async_step_confirm())
        self.assertNotIn("bermuda", result["data_schema"]({}))

    def test_tick_box_is_off_unless_ticked(self):
        result = asyncio.run(self.flow(self.hass([self.bermuda_entry()])).async_step_confirm())
        self.assertIs(result["data_schema"]({})["bermuda"], False)

    def test_a_stopped_bermuda_isnt_offered(self):
        from homeassistant.config_entries import ConfigEntryState

        hass = self.hass([self.bermuda_entry(state=ConfigEntryState.SETUP_ERROR)])
        result = asyncio.run(self.flow(hass).async_step_confirm())
        self.assertNotIn("bermuda", result["data_schema"]({}))

    def test_ticked_adds_it_to_what_bermuda_already_tracks(self):
        hass = self.hass([self.bermuda_entry(["12:34:56:78:9A:BC"])], [self.FORM, self.DONE])
        result = asyncio.run(self.flow(hass).async_step_confirm({"size_ml": 621, "bermuda": True}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["options"], {"size_ml": 621})
        last = hass.config_entries.options.async_configure.call_args_list[-1]
        self.assertEqual(last.args, ("f1", {"configured_devices": ["12:34:56:78:9A:BC", ADDRESS]}))

    def test_unticked_leaves_bermuda_alone(self):
        hass = self.hass([self.bermuda_entry()])
        result = asyncio.run(self.flow(hass).async_step_confirm({"size_ml": 621, "bermuda": False}))
        self.assertEqual(result["type"], "create_entry")
        hass.config_entries.options.async_init.assert_not_called()

    def test_already_tracked_changes_nothing(self):
        hass = self.hass([self.bermuda_entry([ADDRESS])])
        result = asyncio.run(self.flow(hass).async_step_confirm({"size_ml": 621, "bermuda": True}))
        self.assertEqual(result["type"], "create_entry")
        hass.config_entries.options.async_init.assert_not_called()

    def test_a_refusal_says_how_and_still_sets_it_up(self):
        refused = {"type": "form", "step_id": "selectdevices", "errors": {"base": "x"}}
        hass = self.hass([self.bermuda_entry()], [self.FORM, refused])
        flow = self.flow(hass)
        result = asyncio.run(flow.async_step_confirm({"size_ml": 621, "bermuda": True}))
        self.assertEqual(result["step_id"], "bermuda_manual")
        self.assertEqual(result["description_placeholders"], {"address": ADDRESS})
        hass.config_entries.options.async_abort.assert_called_once_with("f1")
        result = asyncio.run(flow.async_step_bermuda_manual({}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {"address": ADDRESS.lower()})

    def test_a_changed_bermuda_falls_back_too(self):
        hass = self.hass([self.bermuda_entry()])
        hass.config_entries.options.async_init.return_value = {"type": "menu", "flow_id": "f1", "menu_options": ["other"]}
        result = asyncio.run(self.flow(hass).async_step_confirm({"size_ml": 621, "bermuda": True}))
        self.assertEqual(result["step_id"], "bermuda_manual")

    def test_an_error_inside_bermuda_falls_back(self):
        hass = self.hass([self.bermuda_entry()], [RuntimeError("boom")])
        result = asyncio.run(self.flow(hass).async_step_confirm({"size_ml": 621, "bermuda": True}))
        self.assertEqual(result["step_id"], "bermuda_manual")


class ProbeChoiceTest(unittest.TestCase):
    def run_probe(self, entries, **data):
        hass = mock.Mock()
        hass.config_entries.async_entries.return_value = entries
        asyncio.run(integration.async_setup(hass, {}))
        handler = hass.services.async_register.call_args.args[2]
        return asyncio.run(handler(SimpleNamespace(data=data)))

    @staticmethod
    def entry(entry_id):
        return SimpleNamespace(
            entry_id=entry_id,
            state=integration.ConfigEntryState.LOADED,
            runtime_data=SimpleNamespace(async_probe=mock.AsyncMock(return_value=entry_id)),
        )

    def test_one_bottle_needs_no_choosing(self):
        self.assertEqual(self.run_probe([self.entry("a")]), "a")

    def test_a_bottle_that_didnt_load_isnt_one_to_probe(self):
        failed = self.entry("b")
        failed.state = integration.ConfigEntryState.SETUP_ERROR
        self.assertEqual(self.run_probe([self.entry("a"), failed]), "a")
        with self.assertRaisesRegex(integration.HomeAssistantError, "No bottle"):
            self.run_probe([failed])

    def test_the_chosen_bottle_is_the_one_probed(self):
        self.assertEqual(self.run_probe([self.entry("a"), self.entry("b")], bottle="b"), "b")

    def test_two_bottles_with_no_choice_refuses_rather_than_guessing(self):
        with self.assertRaisesRegex(integration.HomeAssistantError, "choose"):
            self.run_probe([self.entry("a"), self.entry("b")])

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaisesRegex(integration.HomeAssistantError, "isn't set up"):
            self.run_probe([self.entry("a")], bottle="b")


def record(seconds=30, start=400, stop=300):
    return struct.pack("<BxHiHHHH", 0, 1, seconds, 100, 500, start, stop)


class RelayedTest(Base):
    """What a phone read off the bottle is saved exactly as a sync's would be."""

    FIRMWARE = module.protocol.Firmware.parse(b"90.11")

    def setUp(self):
        super().setUp()
        self.coordinator._store.async_save = mock.AsyncMock()
        self.coordinator._update_device = mock.Mock()

    def relayed(self, rows, **kwargs):
        records = [(module.protocol.parse_packet(data), received) for data, received in rows]
        kwargs.setdefault("connected", True)
        return asyncio.run(self.coordinator.async_relayed(self.FIRMWARE, records, **kwargs))

    def test_a_sip_lands_at_the_time_it_was_drunk(self):
        received = time.time() - 600
        saved, _writes = self.relayed([(record(seconds=40), received)])
        self.assertEqual(saved, 1)
        sip = self.coordinator.log.last
        self.assertAlmostEqual(sip.at.timestamp(), received - 40, places=3)
        self.assertEqual(sip.ml, 155.2)  # a quarter of the default 621 mL
        self.coordinator._store.async_save.assert_awaited()

    def test_the_same_record_sent_twice_counts_once(self):
        received = time.time()
        self.assertEqual(self.relayed([(record(), received)])[0], 1)
        self.assertEqual(self.relayed([(record(), received)])[0], 0)
        self.assertEqual(len(self.coordinator.log.sips), 1)

    def test_empty_and_short_records_are_skipped(self):
        saved, _ = self.relayed([(bytes(16), time.time()), (bytes(1), time.time())])
        self.assertEqual(saved, 0)
        self.assertEqual(self.coordinator.log.raw_records, [])

    def test_raw_records_are_kept_as_a_sync_keeps_them(self):
        self.relayed([(record(), time.time())])
        self.assertEqual(len(self.coordinator.log.raw_records), 1)

    def test_connected_counts_as_a_sync(self):
        self.relayed([], battery=80, status=bytes.fromhex("80200000"))
        coordinator = self.coordinator
        self.assertEqual((coordinator.firmware, coordinator.battery, coordinator.charge), ("90.11", 80, "charging"))
        self.assertIsNotNone(coordinator.last_sync)
        self.assertIsNotNone(coordinator.last_charged)
        self.assertFalse(coordinator._due())

    def test_late_records_from_a_phone_no_longer_connected_change_nothing_else(self):
        saved, writes = self.relayed([(record(), time.time() - 3600)], connected=False, battery=80)
        self.assertEqual((saved, writes), (1, []))
        self.assertIsNone(self.coordinator.last_sync)
        self.assertIsNone(self.coordinator.battery)
        self.assertTrue(self.coordinator._due())

    def test_no_writes_with_reminders_off(self):
        self.assertEqual(self.relayed([])[1], [])

    def test_the_reminder_writes_count_the_sips_just_handed_over(self):
        self.entry.options = {"manage_reminders": True}
        with mock.patch.object(self.coordinator, "_now_seconds", return_value=3600):
            _saved, writes = self.relayed([(record(), time.time())])
        expected = module.protocol.reminder_writes(
            self.coordinator._glow(), self.FIRMWARE, 621, self.coordinator.total_today(), 3600
        )
        self.assertEqual(writes, expected)
        self.assertEqual(writes[0][1], module.protocol.day_total_command(155.2, self.FIRMWARE))

    def test_unsupported_firmware_saves_nothing(self):
        with self.assertRaises(module.session.UnsupportedBottle):
            asyncio.run(
                self.coordinator.async_relayed(
                    module.protocol.Firmware.parse(b"105.0"),
                    [(module.protocol.parse_packet(record()), time.time())],
                    connected=True,
                )
            )
        self.assertEqual(self.coordinator.log.sips, [])

    def test_it_waits_for_a_sync_that_is_running(self):
        async def scenario():
            await self.coordinator._lock.acquire()
            task = asyncio.ensure_future(self.coordinator.async_relayed(self.FIRMWARE, [], connected=True))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.coordinator._lock.release()
            await task

        asyncio.run(scenario())


class RelayWebhookTest(Base):
    """The address a phone calls: there only while the option is on, and it answers each step."""

    def setUp(self):
        super().setUp()
        self.entry.runtime_data = self.coordinator
        self.entry.data["webhook_id"] = "secret"
        patcher = mock.patch.object(relay_webhook, "webhook")
        self.webhook = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, body):
        request = mock.Mock()
        if isinstance(body, Exception):
            request.json = mock.AsyncMock(side_effect=body)
        else:
            request.json = mock.AsyncMock(return_value=body)
        response = asyncio.run(relay_webhook._async_handle(self.entry, mock.Mock(), "secret", request))
        return response.status, json.loads(response.body)

    def test_off_by_default(self):
        relay_webhook.async_update(mock.Mock(), self.entry)
        self.webhook.async_register.assert_not_called()

    def test_registered_while_the_option_is_on_and_removed_when_it_goes_off(self):
        self.entry.options = {"phone_relay": True}
        relay_webhook.async_update(mock.Mock(), self.entry)
        self.assertEqual(self.webhook.async_register.call_args.args[3], "secret")
        self.assertEqual(self.webhook.async_register.call_args.kwargs["allowed_methods"], ("POST",))
        self.entry.options = {}
        self.webhook.reset_mock()
        relay_webhook.async_update(mock.Mock(), self.entry)
        self.webhook.async_unregister.assert_called_once()
        self.webhook.async_register.assert_not_called()

    def test_nothing_to_do_before_an_address_exists(self):
        del self.entry.data["webhook_id"]
        self.entry.options = {"phone_relay": True}
        relay_webhook.async_update(mock.Mock(), self.entry)
        self.webhook.async_register.assert_not_called()

    FIND = {"characteristic": module.protocol.GLOW_NOW, "hex": "b4"}

    def test_the_phone_is_told_to_stand_down_when_the_bottle_is_home(self):
        self.heard(2)
        self.assertEqual(self.call({"step": "check"}), (200, {"go": False, "reason": "in_range", "find": self.FIND}))

    def test_the_phone_goes_ahead_when_the_bottle_is_away(self):
        self.heard(None)
        self.assertEqual(self.call({"step": "check"}), (200, {"go": True, "find": self.FIND}))

    def test_every_check_carries_find_in_the_chosen_style(self):
        self.heard(None)
        self.entry.options = {"glow_light": "effect_flash"}
        self.assertEqual(self.call({"step": "check"})[1]["find"]["hex"], "34")

    def test_find_away_from_home_hands_the_phone_the_write(self):
        self.heard(None)
        self.coordinator.async_find = mock.AsyncMock()
        self.assertEqual(self.call({"step": "find"}), (200, {"done": False, "find": self.FIND}))
        self.coordinator.async_find.assert_not_called()

    def test_find_at_home_is_done_by_home_assistant(self):
        self.heard(2)
        self.coordinator.async_find = mock.AsyncMock()
        self.assertEqual(self.call({"step": "find"}), (200, {"done": True}))
        self.coordinator.async_find.assert_awaited_once()

    def test_find_at_home_that_fails_falls_back_to_the_phone(self):
        self.heard(2)
        self.coordinator.async_find = mock.AsyncMock(side_effect=module.HomeAssistantError("no proxy"))
        self.assertEqual(self.call({"step": "find"}), (200, {"done": False, "find": self.FIND}))

    def test_begin_gives_the_instructions(self):
        status, reply = self.call({"step": "begin", "firmware": "90.11"})
        self.assertEqual((status, reply["go"], reply["ask"]), (200, True, "57"))
        self.assertEqual(self.call({"step": "begin", "firmware": "105.0"})[1]["go"], False)

    def test_sips_are_saved_and_the_writes_come_back(self):
        self.coordinator._store.async_save = mock.AsyncMock()
        self.coordinator._update_device = mock.Mock()
        self.entry.options = {"manage_reminders": True}
        body = {
            "step": "sips",
            "firmware": "90.11",
            "connected": True,
            "battery": 77,
            "commands": "80000000",
            "records": [{"hex": record().hex(), "received": time.time()}],
        }
        status, reply = self.call(body)
        self.assertEqual((status, reply["received"], reply["saved"]), (200, 1, 1))
        self.assertEqual(len(reply["writes"]), 5 + module.protocol.SCHEDULE_SLOTS)
        self.assertEqual((self.coordinator.battery, self.coordinator.charge), (77, "not_charging"))

    def test_a_refusal_points_at_the_record_at_fault(self):
        good = {"hex": record().hex(), "received": time.time()}
        body = {"step": "sips", "firmware": "90.11", "records": [good, {"hex": "zz", "received": 1}]}
        status, reply = self.call(body)
        self.assertEqual((status, reply["record"]), (400, 1))
        ahead = {"hex": record().hex(), "received": time.time() + 3600}
        status, reply = self.call({"step": "sips", "firmware": "90.11", "records": [ahead]})
        self.assertEqual(status, 400)
        self.assertNotIn("record", reply)
        self.assertEqual(self.coordinator.log.sips, [])

    def test_while_reloading_the_phone_is_told_to_keep_its_records(self):
        self.coordinator._store.async_save = mock.AsyncMock()
        asyncio.run(self.coordinator.async_close())
        body = {
            "step": "sips",
            "firmware": "90.11",
            "connected": True,
            "records": [{"hex": record().hex(), "received": time.time()}],
        }
        status, reply = self.call(body)
        # Neither the 200 that lets the phone drop them nor the 400 that has it put them aside.
        self.assertEqual(status, 503)
        self.assertIn("error", reply)
        self.assertNotIn("received", reply)
        self.assertEqual(self.coordinator.log.sips, [])

    def test_bad_requests_are_refused_and_save_nothing(self):
        for body in (
            ValueError("not json"),
            ["a list"],
            {"step": "nonsense"},
            {"step": "begin"},
            {"step": "sips", "firmware": "90.11", "records": [{"hex": "zz", "received": 1}]},
            {"step": "sips", "firmware": "105.0", "records": []},
        ):
            self.assertEqual(self.call(body)[0], 400, body)
        self.assertEqual(self.coordinator.log.sips, [])


class RoomTest(Base):
    """The Room sensor repeats Bermuda's Area sensor for this bottle, and follows it if it comes or goes."""

    def setUp(self):
        super().setUp()
        self.source = None
        self.states = {}
        patcher = mock.patch.object(
            sensor_platform.bermuda, "area_entity_id", side_effect=lambda _hass, _address: self.source
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.unfollow = mock.Mock()
        patcher = mock.patch.object(sensor_platform, "async_track_state_change_event", return_value=self.unfollow)
        self.track = patcher.start()
        self.addCleanup(patcher.stop)
        self.room = sensor_platform.RoomSensor(self.coordinator, sensor_platform.ROOM)
        self.room.hass = mock.Mock()
        self.room.hass.states.get = lambda entity_id: self.states.get(entity_id)
        self.room.async_write_ha_state = mock.Mock()

    def start(self):
        asyncio.run(self.room.async_added_to_hass())
        return self.room.hass.bus.async_listen.call_args.args[1]

    def test_shows_bermudas_room(self):
        self.source = "sensor.bottle_area"
        self.states["sensor.bottle_area"] = SimpleNamespace(state="Kitchen")
        self.start()
        self.assertTrue(self.room.available)
        self.assertEqual(self.room.native_value, "Kitchen")
        self.assertEqual(self.room.extra_state_attributes, {"source": "sensor.bottle_area"})
        self.assertEqual(self.track.call_args.args[1], ["sensor.bottle_area"])

    def test_no_room_from_bermuda_is_unknown(self):
        self.source = "sensor.bottle_area"
        self.start()
        self.assertIsNone(self.room.native_value)
        for state in ("unknown", "unavailable", "not_home", ""):
            self.states["sensor.bottle_area"] = SimpleNamespace(state=state)
            self.assertIsNone(self.room.native_value, state)

    def test_unavailable_until_bermuda_tracks_the_bottle_then_follows_it(self):
        registry_changed = self.start()
        self.assertFalse(self.room.available)
        self.track.assert_not_called()
        self.source = "sensor.bottle_area"
        registry_changed(None)
        self.assertTrue(self.room.available)
        self.room.async_write_ha_state.assert_called_once()
        self.source = "sensor.renamed_area"
        registry_changed(None)
        self.unfollow.assert_called_once()
        self.assertEqual(self.track.call_args.args[1], ["sensor.renamed_area"])

    def test_an_unrelated_registry_change_does_nothing(self):
        self.source = "sensor.bottle_area"
        registry_changed = self.start()
        registry_changed(None)
        self.assertEqual(self.track.call_count, 1)
        self.room.async_write_ha_state.assert_not_called()

    def test_only_offered_where_bermuda_is_set_up(self):
        entry = SimpleNamespace(runtime_data=self.coordinator)
        for installed, count in ((False, 1), (True, 2)):
            added = []
            with mock.patch.object(sensor_platform.bermuda, "installed", return_value=installed):
                asyncio.run(sensor_platform.async_setup_entry(mock.Mock(), entry, lambda e: added.append(list(e))))
            self.assertEqual(len(added), count)
            self.assertEqual(any(isinstance(x, sensor_platform.RoomSensor) for batch in added for x in batch), installed)


class RelayOptionsTest(unittest.TestCase):
    """Turning the relay on shows its address, made once and kept."""

    OPTIONS = {**DEFAULTS, "phone_relay": True}

    def flow(self, data):
        flow = config_flow.HidrateOptionsFlow()
        flow.hass = mock.Mock()
        entry = SimpleNamespace(data=data, options={})
        flow.hass.config_entries.async_update_entry = lambda _entry, data: setattr(entry, "data", data)
        patcher = mock.patch.object(config_flow.HidrateOptionsFlow, "config_entry", entry, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return flow, entry

    def test_off_saves_straight_away(self):
        flow, entry = self.flow({"address": ADDRESS})
        result = asyncio.run(flow.async_step_init({**DEFAULTS}))
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("webhook_id", entry.data)

    def test_on_makes_an_address_shows_it_then_saves(self):
        flow, entry = self.flow({"address": ADDRESS})
        with (
            mock.patch.object(config_flow.webhook, "async_generate_id", return_value="made"),
            mock.patch.object(
                config_flow.relay_webhook, "address_for", return_value="https://example.invalid/api/webhook/made"
            ),
        ):
            shown = asyncio.run(flow.async_step_init(dict(self.OPTIONS)))
        self.assertEqual((shown["type"], shown["step_id"]), ("form", "phone_relay"))
        self.assertIn("made", shown["description_placeholders"]["address"])
        self.assertEqual(entry.data, {"address": ADDRESS, "webhook_id": "made"})
        saved = asyncio.run(flow.async_step_phone_relay({}))
        self.assertEqual((saved["type"], saved["data"]), ("create_entry", self.OPTIONS))

    def test_an_address_already_made_is_kept(self):
        flow, entry = self.flow({"address": ADDRESS, "webhook_id": "kept"})
        with mock.patch.object(config_flow.relay_webhook, "address_for", side_effect=lambda _hass, made: made):
            shown = asyncio.run(flow.async_step_init(dict(self.OPTIONS)))
        self.assertEqual(shown["description_placeholders"]["address"], "kept")
        self.assertEqual(entry.data["webhook_id"], "kept")


if __name__ == "__main__":
    unittest.main()
