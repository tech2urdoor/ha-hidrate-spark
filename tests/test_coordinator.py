"""When the coordinator decides to sync. Needs a real Home Assistant installed, as tests/check_imports.py does.

    python tests/test_coordinator.py
"""

import asyncio
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


class ProbeChoiceTest(unittest.TestCase):
    def run_probe(self, entries, **data):
        hass = mock.Mock()
        hass.config_entries.async_loaded_entries.return_value = entries
        asyncio.run(integration.async_setup(hass, {}))
        handler = hass.services.async_register.call_args.args[2]
        return asyncio.run(handler(SimpleNamespace(data=data)))

    @staticmethod
    def entry(entry_id):
        return SimpleNamespace(
            entry_id=entry_id, runtime_data=SimpleNamespace(async_probe=mock.AsyncMock(return_value=entry_id))
        )

    def test_one_bottle_needs_no_choosing(self):
        self.assertEqual(self.run_probe([self.entry("a")]), "a")

    def test_the_chosen_bottle_is_the_one_probed(self):
        self.assertEqual(self.run_probe([self.entry("a"), self.entry("b")], bottle="b"), "b")

    def test_two_bottles_with_no_choice_refuses_rather_than_guessing(self):
        with self.assertRaisesRegex(integration.HomeAssistantError, "choose"):
            self.run_probe([self.entry("a"), self.entry("b")])

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaisesRegex(integration.HomeAssistantError, "isn't set up"):
            self.run_probe([self.entry("a")], bottle="b")


if __name__ == "__main__":
    unittest.main()
