"""When the coordinator decides to sync. Needs a real Home Assistant installed, as tests/check_imports.py does.

    python tests/test_coordinator.py
"""

import asyncio
import sys
import time
import unittest
from datetime import time as dt_time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.hidrate_spark import coordinator as module  # noqa: E402
from custom_components.hidrate_spark import number as number_platform  # noqa: E402
from custom_components.hidrate_spark import select as select_platform  # noqa: E402
from custom_components.hidrate_spark import switch as switch_platform  # noqa: E402
from custom_components.hidrate_spark import time as time_platform  # noqa: E402
from custom_components.hidrate_spark.const import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
