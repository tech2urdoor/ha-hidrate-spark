"""When the coordinator decides to sync. Needs a real Home Assistant installed, as tests/check_imports.py does.

    python tests/test_coordinator.py
"""

import asyncio
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.hidrate_spark import coordinator as module  # noqa: E402
from custom_components.hidrate_spark.const import (  # noqa: E402
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
