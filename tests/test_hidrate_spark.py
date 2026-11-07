#!/usr/bin/env python3
"""Pins the bottle's bytes and the sip handling. Runs without Home Assistant:

    python3 tests/test_hidrate_spark.py
"""
import ast
import asyncio
import json
import re
import struct
import subprocess
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hidrate_spark"
package = types.ModuleType("hidrate_spark")
package.__path__ = [str(COMPONENT)]
sys.modules["hidrate_spark"] = package

from hidrate_spark import protocol as p  # noqa: E402
from hidrate_spark import session as s  # noqa: E402
from hidrate_spark.sip_log import SipLog  # noqa: E402

NORDIC = p.Firmware.parse(b"85.3")
CYPRESS = p.Firmware.parse(b"55.3")
CYPRESS_NEW = p.Firmware.parse(b"55.9")
STEEL = p.Firmware.parse(b"53.63")  # the one firmware that has been run on a real bottle
LOCAL = ZoneInfo("Asia/Tokyo")
GLOW = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600)


def sip_packet(remaining=0, seconds=30, low=100, high=500, start=400, stop=300, unused=1):
    return struct.pack("<BxHiHHHH", remaining, unused, seconds, low, high, start, stop)


class FirmwareTest(unittest.TestCase):
    def test_parses_major_and_minor(self):
        self.assertEqual((NORDIC.major, NORDIC.minor, NORDIC.raw), (85, 3, "85.3"))
        self.assertEqual(p.Firmware.parse(b"85.3\x00\x00").raw, "85.3")

    def test_unreadable_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            p.Firmware.parse(b"banana")

    def test_supported_kinds(self):
        for good in (b"40.0", b"55.1", b"85.3", b"99.0"):
            self.assertTrue(p.Firmware.parse(good).supported, good)
        for bad in (b"7.24", b"25.0", b"35.0", b"100.0", b"110.1"):
            self.assertFalse(p.Firmware.parse(bad).supported, bad)

    def test_history_command(self):
        self.assertEqual(p.history_command(NORDIC), 0x57)
        self.assertEqual(p.history_command(p.Firmware.parse(b"100.0")), 0x55)
        self.assertEqual(p.history_command(p.Firmware.parse(b"25.0")), 0x55)
        self.assertEqual(p.history_command(p.Firmware.parse(b"7.24")), 0x57)
        self.assertEqual(p.history_command(p.Firmware.parse(b"7.23")), 0x55)


class TestedBottleTest(unittest.TestCase):
    def test_the_firmware_seen_on_a_real_bottle(self):
        self.assertEqual((STEEL.major, STEEL.minor), (53, 63))
        self.assertTrue(STEEL.supported)
        self.assertEqual(p.history_command(STEEL), 0x57)
        self.assertEqual(p.calibrate_empty_command(STEEL), bytes((64,)))
        self.assertEqual(p.schedule_rows(GLOW, STEEL, 621, 0)[0][1], 0xB4)


class PacketTest(unittest.TestCase):
    def test_full_packet_layout(self):
        raw = bytes.fromhex("0300" "0100" "1e000000" "6400" "f401" "9001" "2c01")
        packet = p.parse_packet(raw)
        self.assertTrue(packet.complete)
        self.assertEqual(packet.remaining, 3)
        self.assertEqual(packet.unused, 1)
        self.assertEqual(packet.seconds_ago, 30)
        self.assertEqual(packet.raw_key, (100, 500, 400, 300))

    def test_short_packet_is_only_a_count(self):
        packet = p.parse_packet(b"\x05\x00\x00")
        self.assertFalse(packet.complete)
        self.assertEqual(packet.remaining, 5)

    def test_amount_is_the_fall_in_level(self):
        packet = p.parse_packet(sip_packet())
        self.assertAlmostEqual(p.amount_ml(packet, 621), 155.25)

    def test_flat_sensor_range_is_zero_not_a_crash(self):
        packet = p.parse_packet(sip_packet(low=500, high=500))
        self.assertEqual(p.amount_ml(packet, 621), 0.0)

    def test_levels_clamp_to_the_bottle(self):
        packet = p.parse_packet(sip_packet(start=900, stop=50))
        self.assertAlmostEqual(p.amount_ml(packet, 621), 621.0)

    def test_refill_is_negative(self):
        packet = p.parse_packet(sip_packet(start=200, stop=400))
        self.assertLess(p.amount_ml(packet, 621), 0)

    def test_empty_record(self):
        empty = p.parse_packet(sip_packet(seconds=0, start=300, stop=300, unused=0))
        self.assertTrue(p.is_empty(empty, 621))
        self.assertFalse(p.is_empty(p.parse_packet(sip_packet()), 621))


class CommandTest(unittest.TestCase):
    def test_day_total(self):
        self.assertEqual(p.day_total_command(1000, NORDIC), bytes((0x21, 0, 168)))
        self.assertEqual(p.day_total_command(1000, p.Firmware.parse(b"7.0")), bytes((0x21, 0, 140)))

    def test_goal(self):
        self.assertEqual(p.goal_command(2000, 621), bytes((0x22, 1, 66)))

    def test_goal_glow(self):
        self.assertEqual(p.goal_glow_command(True), bytes((0x93, 61)))
        self.assertEqual(p.goal_glow_command(False), bytes((0x92,)))

    def test_sip_glow(self):
        self.assertEqual(p.sip_glow_command(True), bytes((0xB1,)))
        self.assertEqual(p.sip_glow_command(False), bytes((0xB0,)))

    def test_clock(self):
        self.assertEqual(p.clock_command(34567).hex(), "77000000078700" + "00")

    def test_calibration(self):
        self.assertEqual(p.calibrate_empty_command(NORDIC), bytes((64,)))
        self.assertEqual(p.calibrate_empty_command(p.Firmware.parse(b"45.0")), bytes((160,)))
        self.assertEqual(p.calibrate_full_command(), bytes((0x41,)))

    def test_find_uses_the_glow_light(self):
        self.assertEqual(p.find_command(p.LIGHTS["high"]), bytes((0xB4,)))


class ScheduleTest(unittest.TestCase):
    def test_twelve_rows_always(self):
        self.assertEqual(len(p.schedule_rows(GLOW, NORDIC, 621, 0)), 12)

    def test_first_and_last_row_bytes(self):
        rows = p.schedule_rows(GLOW, NORDIC, 621, 0)
        self.assertEqual(rows[0], bytes((0, 0xB4, 20, 0)) + struct.pack("<I", 28800))
        self.assertEqual(rows[11], bytes((11, 0xB4, 241, 0)) + struct.pack("<I", 68400))

    def test_spare_slots_are_blank_but_keep_the_light(self):
        glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600, frequency_min=240)
        rows = p.schedule_rows(glow, NORDIC, 621, 0)
        self.assertEqual(rows[2][4:], struct.pack("<I", 7 * 3600 + 3 * 240 * 60))
        self.assertEqual(rows[3], bytes((3, 0xB4, 0, 0, 0, 0, 0, 0)))

    def test_no_frequency_means_no_reminders(self):
        glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600, frequency_min=0)
        for slot, row in enumerate(p.schedule_rows(glow, NORDIC, 621, 0)):
            self.assertEqual(row, bytes((slot, 0xB4, 0, 0, 0, 0, 0, 0)))

    def test_glow_always_sets_ff(self):
        glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600, always=True)
        rows = p.schedule_rows(glow, NORDIC, 621, 0)
        self.assertEqual(rows[0][2:4], b"\xff\xff")
        self.assertEqual(rows[-1][2:4], b"\xff\xff")

    def test_schedule_past_midnight_wraps(self):
        glow = p.GlowSettings(goal_ml=2000, wake_s=22 * 3600, sleep_s=2 * 3600)
        rows = p.schedule_rows(glow, NORDIC, 621, 0)
        self.assertEqual(rows[0][4:], struct.pack("<I", 23 * 3600))
        self.assertEqual(rows[2][4:], struct.pack("<I", 3600))

    def test_cypress_low_minor_uses_passed_flag(self):
        rows = p.schedule_rows(GLOW, CYPRESS, 621, now_s=30000)
        self.assertEqual(rows[0], bytes((0, 1, 20, 0)) + struct.pack("<I", 28800))
        self.assertEqual(rows[1][1], 0)

    def test_cypress_high_minor_uses_light(self):
        self.assertEqual(p.schedule_rows(GLOW, CYPRESS_NEW, 621, 0)[0][1], 0xB4)

    def test_write_order(self):
        writes = p.reminder_writes(GLOW, NORDIC, 621, 1000, 34567)
        self.assertEqual(writes[0], (p.COMMANDS, p.day_total_command(1000, NORDIC)))
        self.assertEqual(writes[1], (p.SCHEDULE, bytes((0x93, 61))))
        self.assertEqual(writes[2], (p.COMMANDS, p.goal_command(2000, 621)))
        self.assertEqual(writes[3], (p.SCHEDULE, p.clock_command(34567)))
        self.assertEqual(writes[4][1][0], 0)
        self.assertEqual(writes[15][1][0], 11)
        self.assertEqual(writes[16], (p.COMMANDS, bytes((0xB1,))))
        self.assertEqual(len(writes), 17)

    def test_clock_is_left_alone_once_the_goal_is_met(self):
        over = p.reminder_writes(GLOW, NORDIC, 621, 2500, 34567)
        self.assertEqual(len(over), 16)
        self.assertNotIn(p.clock_command(34567), [payload for _, payload in over])
        glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600, on_goal=False)
        self.assertIn(p.clock_command(34567), [w[1] for w in p.reminder_writes(glow, NORDIC, 621, 2500, 34567)])

    def test_nothing_touches_the_dangerous_characteristics(self):
        for firmware in (NORDIC, CYPRESS, CYPRESS_NEW):
            targets = {uuid for uuid, _ in p.reminder_writes(GLOW, firmware, 621, 1000, 0)}
            self.assertFalse(targets & p.NEVER_WRITE)


class FakeClient:
    """Answers reads from a table. Each write of the history command releases the next scripted notification."""

    def __init__(self, firmware=b"85.3", notifications=(), fail_on_write=None, fail_stop=False):
        self.reads = {
            p.FIRMWARE_REVISION: firmware,
            p.SERIAL_NUMBER: b"SERIAL1\x00",
            p.BATTERY_LEVEL: bytes((77,)),
        }
        self.notifications = list(notifications)
        self.fail_on_write = fail_on_write
        self.fail_stop = fail_stop
        self.writes = []
        self.read_log = []
        self.callback = None
        self.stopped = False
        self.services = []

    async def read_gatt_char(self, uuid):
        self.read_log.append(uuid)
        if uuid not in self.reads:
            raise OSError("not readable")
        return self.reads[uuid]

    async def write_gatt_char(self, uuid, data):
        self.writes.append((uuid, bytes(data)))
        if len(self.writes) == self.fail_on_write:
            raise OSError("link lost")
        if uuid == p.SIPS:
            for packet in self.notifications:
                self.callback(uuid, bytearray(packet))
            self.notifications = []

    async def start_notify(self, uuid, callback):
        self.callback = callback

    async def stop_notify(self, uuid):
        self.stopped = True
        if self.fail_stop:
            raise OSError("link lost")


def read(client, firmware=NORDIC, size=621):
    got = []
    count = asyncio.run(
        s.read_sips(client, firmware, size, lambda pk, at: got.append((pk, at)),
                    idle_seconds=0.01, clock=lambda: 1000.0)
    )
    return count, got


class ReadSipsTest(unittest.TestCase):
    def test_hands_over_each_sip_and_stops_when_idle(self):
        client = FakeClient(notifications=[sip_packet(1), sip_packet(0, seconds=60)])
        count, got = read(client)
        self.assertEqual(count, 2)
        self.assertEqual([pk.seconds_ago for pk, _ in got], [30, 60])
        self.assertEqual(got[0][1], 1000.0)
        self.assertEqual(client.writes, [(p.SIPS, b"\x57")])
        self.assertTrue(client.stopped)

    def test_count_of_zero_finishes(self):
        count, got = read(FakeClient(notifications=[b"\x00\x00"]))
        self.assertEqual((count, got), (0, []))

    def test_count_above_zero_asks_again(self):
        client = FakeClient(notifications=[b"\x02\x00"])
        read(client)
        self.assertEqual(client.writes, [(p.SIPS, b"\x57"), (p.SIPS, b"\x57")])

    def test_empty_record_with_more_to_come_asks_again(self):
        client = FakeClient(notifications=[sip_packet(1, seconds=0, start=300, stop=300, unused=0)])
        count, _ = read(client)
        self.assertEqual(count, 0)
        self.assertEqual(len(client.writes), 2)

    def test_empty_record_with_nothing_left_finishes(self):
        client = FakeClient(notifications=[sip_packet(0, seconds=0, start=300, stop=300, unused=0)])
        count, _ = read(client)
        self.assertEqual(count, 0)
        self.assertEqual(len(client.writes), 1)

    def test_sips_already_handed_over_survive_a_dropped_link(self):
        client = FakeClient(notifications=[sip_packet(1), b"\x01\x00"], fail_on_write=2)
        got = []
        with self.assertRaises(OSError):
            asyncio.run(s.read_sips(client, NORDIC, 621, lambda pk, at: got.append(pk), idle_seconds=0.01))
        self.assertEqual(len(got), 1)
        self.assertTrue(client.stopped)

    def test_a_failing_unsubscribe_does_not_lose_the_result(self):
        client = FakeClient(notifications=[sip_packet(0)], fail_stop=True)
        count, got = read(client)
        self.assertEqual((count, len(got)), (1, 1))


class SyncTest(unittest.TestCase):
    def run_sync(self, client, glow=None, known_serial=None):
        sips = []
        result = asyncio.run(
            s.sync(client, size_ml=621, on_sip=lambda pk, at: sips.append(pk),
                   total_today=lambda: 300.0 + 155.25 * len(sips), now_seconds=lambda: 34567,
                   glow=glow, known_serial=known_serial, idle_seconds=0.01)
        )
        return result, sips

    def test_read_only_without_reminders(self):
        client = FakeClient(notifications=[sip_packet(0)])
        result, sips = self.run_sync(client)
        self.assertEqual(len(sips), 1)
        self.assertEqual((result.battery, result.serial, result.firmware.raw), (77, "SERIAL1", "85.3"))
        self.assertEqual(client.writes, [(p.SIPS, b"\x57")])

    def test_reminders_include_the_sips_just_read(self):
        client = FakeClient(notifications=[sip_packet(0)])
        self.run_sync(client, glow=GLOW)
        self.assertEqual(client.writes[1], (p.COMMANDS, p.day_total_command(455.25, NORDIC)))
        self.assertEqual(len(client.writes), 18)

    def test_unsupported_firmware_takes_nothing(self):
        client = FakeClient(firmware=b"105.0", notifications=[sip_packet(0)])
        with self.assertRaises(s.UnsupportedBottle):
            self.run_sync(client, glow=GLOW)
        self.assertEqual(client.writes, [])

    def test_serial_is_read_once(self):
        client = FakeClient()
        result, _ = self.run_sync(client, known_serial="KEPT")
        self.assertEqual(result.serial, "KEPT")
        self.assertNotIn(p.SERIAL_NUMBER, client.read_log)

    def test_bad_battery_reads_as_unknown(self):
        client = FakeClient()
        client.reads[p.BATTERY_LEVEL] = bytes((200,))
        self.assertIsNone(self.run_sync(client)[0].battery)


class FakeService:
    uuid = "0000180f-0000-1000-8000-00805f9b34fb"
    characteristics = [types.SimpleNamespace(uuid=p.BATTERY_LEVEL, properties=["read", "notify"])]


class ProbeTest(unittest.TestCase):
    """Made-up identity. The report gets pasted into public issues, so none of it may survive."""

    ADDRESS = "AA:BB:CC:DD:EE:FF"
    NAME = "h2oEEFF"

    def probe(self, client):
        private = s.private_values(self.NAME, "kitchen-proxy", address=self.ADDRESS)
        return asyncio.run(s.probe(client, private))

    def test_reads_only_the_safe_list_and_reports_failures(self):
        client = FakeClient()
        report = self.probe(client)
        self.assertEqual(report["firmware"], {"hex": b"85.3".hex(), "text": "85.3"})
        self.assertIn("error", report["schedule"])
        self.assertNotIn(p.SIPS, client.read_log)
        self.assertNotIn(p.FIRMWARE_UPDATE, client.read_log)
        self.assertEqual(client.writes, [])

    def test_serial_is_never_reported(self):
        report = self.probe(FakeClient())
        self.assertEqual(report["serial"], {"redacted": True, "length": 7})
        self.assertNotIn("SERIAL1", json.dumps(report))
        self.assertNotIn(b"SERIAL1".hex(), json.dumps(report))

    def test_private_values_inside_other_characteristics_are_removed(self):
        client = FakeClient()
        client.reads[p.SETTINGS] = b"\x01" + b"SERIAL1" + b"\x02"
        client.reads[p.COMMANDS] = bytes.fromhex("aabbccddeeff")
        client.reads[p.RUNNING_TOTAL] = bytes.fromhex("ffeeddccbbaa")
        report = self.probe(client)
        self.assertEqual(report["settings"]["hex"], f"01{s.REDACTED}02")
        self.assertEqual(report["commands"]["hex"], s.REDACTED)
        self.assertEqual(report["running_total"]["hex"], s.REDACTED)

    def test_errors_lose_the_address_name_and_proxy(self):
        class Noisy(FakeClient):
            async def read_gatt_char(self, uuid):
                if uuid == p.BATTERY_LEVEL:
                    raise OSError("kitchen-proxy: h2oEEFF - AA:BB:CC:DD:EE:FF: read failed")
                return await super().read_gatt_char(uuid)

        report = self.probe(Noisy())
        self.assertEqual(report["battery"]["error"], f"{s.REDACTED}: {s.REDACTED} - {s.REDACTED}: read failed")

    def test_gatt_table_survives(self):
        client = FakeClient()
        client.services = [FakeService()]
        report = self.probe(client)
        self.assertEqual(report["gatt"][FakeService.uuid], [f"{p.BATTERY_LEVEL} read,notify"])

    def test_addresses_are_removed_even_when_not_listed(self):
        self.assertEqual(s.redact_text("at 12:34:56:78:9a:bc now"), f"at {s.REDACTED} now")
        self.assertEqual(s.redact_text("at 12-34-56-78-9A-BC"), f"at {s.REDACTED}")
        self.assertEqual(s.redact_text("name H2OABCD"), f"name {s.REDACTED}")
        self.assertEqual(s.redact_text(p.SIPS), p.SIPS)

    def test_mask_name_keeps_only_the_start(self):
        self.assertEqual(s.mask_name(self.NAME), "h2o****")
        self.assertIsNone(s.mask_name(None))


class ColourBytesTest(unittest.TestCase):
    FACTORY = bytes.fromhex("6d0200000000")

    def test_pins_the_two_colours_seen_written(self):
        self.assertEqual(p.colour_settings(self.FACTORY, "Blue").hex(), "6d027f07bf03")
        self.assertEqual(p.colour_settings(self.FACTORY, "Rainbow").hex(), "6d020100bf03")

    def test_the_size_bytes_are_kept_as_read(self):
        self.assertEqual(p.colour_settings(bytes.fromhex("aabb00000000"), "Blue")[:2], bytes.fromhex("aabb"))

    def test_any_other_length_is_refused(self):
        for bad in (b"", self.FACTORY[:5], self.FACTORY + b"\0"):
            with self.assertRaises(ValueError):
                p.colour_settings(bad, "Blue")

    def test_only_the_family_seen_can_change_colour(self):
        for raw, allowed in ((b"49.9", False), (b"50.0", True), (b"59.9", True), (b"60.0", False), (b"85.3", False)):
            self.assertEqual(p.Firmware.parse(raw).can_set_colour, allowed, raw)


class EchoingClient(FakeClient):
    """Keeps what's written to SETTINGS, as a real bottle would, unless told to ignore it."""

    def __init__(self, firmware=b"53.63", settings=b"\x6d\x02\0\0\0\0", ignore_writes=False):
        super().__init__(firmware=firmware)
        self.reads[p.SETTINGS] = settings
        self.ignore_writes = ignore_writes

    async def write_gatt_char(self, uuid, data):
        await super().write_gatt_char(uuid, data)
        if uuid == p.SETTINGS and not self.ignore_writes:
            self.reads[uuid] = bytes(data)


class SetColourTest(unittest.TestCase):
    def test_writes_settings_once_and_reads_it_back(self):
        client = EchoingClient()
        asyncio.run(s.set_colour(client, "Blue"))
        self.assertEqual(client.writes, [(p.SETTINGS, bytes.fromhex("6d027f07bf03"))])
        self.assertEqual(client.read_log[-1], p.SETTINGS)

    def test_a_colour_already_set_writes_nothing(self):
        client = EchoingClient(settings=bytes.fromhex("6d027f07bf03"))
        asyncio.run(s.set_colour(client, "Blue"))
        self.assertEqual(client.writes, [])

    def test_a_write_that_does_not_read_back_is_an_error(self):
        with self.assertRaises(s.ColourNotSet):
            asyncio.run(s.set_colour(EchoingClient(ignore_writes=True), "Blue"))

    def test_other_firmware_is_refused_before_anything_is_read_or_written(self):
        client = EchoingClient(firmware=b"85.3")
        with self.assertRaises(s.ColourNotSet):
            asyncio.run(s.set_colour(client, "Blue"))
        self.assertEqual(client.writes, [])
        self.assertNotIn(p.SETTINGS, client.read_log)

    def test_an_unexpected_layout_is_refused_without_writing(self):
        client = EchoingClient(settings=b"\x6d\x02\0\0")
        with self.assertRaises(s.ColourNotSet):
            asyncio.run(s.set_colour(client, "Blue"))
        self.assertEqual(client.writes, [])

    def test_it_takes_no_sips(self):
        client = EchoingClient()
        asyncio.run(s.set_colour(client, "Blue"))
        self.assertNotIn(p.SIPS, [uuid for uuid, _ in client.writes])
        self.assertIsNone(client.callback)


class SyncLeavesSettingsAloneTest(unittest.TestCase):
    def test_a_sync_neither_reads_nor_writes_settings(self):
        client = EchoingClient()
        glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600)
        asyncio.run(
            s.sync(
                client,
                size_ml=621,
                on_sip=lambda *_: None,
                total_today=lambda: 0,
                now_seconds=lambda: 0,
                glow=glow,
                idle_seconds=0.01,
            )
        )
        self.assertNotIn(p.SETTINGS, client.read_log)
        self.assertNotIn(p.SETTINGS, [uuid for uuid, _ in client.writes])


class WriteTargetsTest(unittest.TestCase):
    """docs/what-it-writes.md is a promise, so the code that writes is held to it."""

    def test_settings_is_written_only_by_set_colour(self):
        tree = ast.parse((COMPONENT / "session.py").read_text())
        writers = set()
        for function in ast.walk(tree):
            if isinstance(function, ast.AsyncFunctionDef):
                for node in ast.walk(function):
                    if (
                        isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "write_gatt_char"
                        and getattr(node.args[0], "id", "") == "SETTINGS"
                    ):
                        writers.add(function.name)
        self.assertEqual(writers, {"set_colour"})

    def test_writes_go_only_where_the_doc_says(self):
        allowed = {"SIPS", "COMMANDS", "GLOW_NOW", "SETTINGS", "uuid"}
        found = set()
        for name in ("coordinator.py", "session.py"):
            for node in ast.walk(ast.parse((COMPONENT / name).read_text())):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "write_gatt_char":
                    target = node.args[0]
                    found.add(target.attr if isinstance(target, ast.Attribute) else target.id)
        self.assertEqual(found, allowed)

    def test_the_uuid_written_in_a_loop_comes_from_the_reminders(self):
        for firmware in (NORDIC, CYPRESS, CYPRESS_NEW):
            for on_goal in (True, False):
                glow = p.GlowSettings(goal_ml=2000, wake_s=7 * 3600, sleep_s=22 * 3600, on_goal=on_goal)
                targets = {uuid for uuid, _ in p.reminder_writes(glow, firmware, 621, 2500, 0)}
                self.assertEqual(targets, {p.COMMANDS, p.SCHEDULE})


class RepositoryTest(unittest.TestCase):
    def test_no_real_looking_address_is_committed(self):
        """A pasted probe or log in a fixture would publish someone's bottle."""
        placeholders = {"AA:BB:CC:DD:EE:FF", "12:34:56:78:9A:BC", "12-34-56-78-9A-BC"}
        try:
            listed = subprocess.run(
                ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.split("\n")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        for name in filter(None, listed):
            path = ROOT / name
            if not path.is_file() or path.suffix == ".png":
                continue
            for found in re.findall(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b", path.read_text()):
                self.assertIn(found.upper(), placeholders, name)


class SipLogTest(unittest.TestCase):
    def setUp(self):
        self.log = SipLog(LOCAL)
        self.at = datetime(2026, 11, 7, 10, 0, tzinfo=LOCAL)
        self.packet = p.parse_packet(sip_packet())

    def test_adds_a_sip(self):
        sip = self.log.add(self.packet, self.at, 621)
        self.assertEqual(sip.ml, 155.2)
        self.assertEqual(self.log.total_for(date(2026, 11, 7)), 155.2)
        self.assertEqual(self.log.last, sip)

    def test_refill_is_not_a_sip(self):
        refill = p.parse_packet(sip_packet(start=200, stop=400))
        self.assertIsNone(self.log.add(refill, self.at, 621))
        self.assertEqual(self.log.daily, {})

    def test_same_sip_read_twice_counts_once(self):
        self.log.add(self.packet, self.at, 621)
        self.assertIsNone(self.log.add(self.packet, self.at + timedelta(seconds=90), 621))
        self.assertEqual(self.log.total_for(date(2026, 11, 7)), 155.2)

    def test_same_levels_later_are_a_new_sip(self):
        self.log.add(self.packet, self.at, 621)
        self.assertIsNotNone(self.log.add(self.packet, self.at + timedelta(seconds=300), 621))

    def test_late_sip_lands_on_the_day_it_was_drunk(self):
        drunk = datetime(2026, 11, 6, 23, 50, tzinfo=LOCAL)
        self.log.add(self.packet, self.at, 621)
        self.log.add(p.parse_packet(sip_packet(stop=200)), drunk, 621)
        self.assertEqual(self.log.total_for(date(2026, 11, 6)), 310.5)
        self.assertEqual(self.log.total_for(date(2026, 11, 7)), 155.2)
        self.assertEqual(self.log.last.at, self.at)

    def test_day_follows_local_time_not_utc(self):
        just_after_midnight = datetime(2026, 11, 7, 0, 5, tzinfo=LOCAL).astimezone(timezone.utc)
        self.log.add(self.packet, just_after_midnight, 621)
        self.assertEqual(self.log.total_for(date(2026, 11, 7)), 155.2)

    def test_recent_totals_cover_the_window(self):
        self.log.daily = {"2026-10-01": 500.0, "2026-11-01": 1000.4, "2026-11-07": 800.6}
        self.assertEqual(
            self.log.recent_totals(date(2026, 11, 7)),
            {"2026-11-01": 1000, "2026-11-07": 801},
        )

    def test_prune_drops_sips_and_keeps_totals(self):
        self.log.add(self.packet, self.at, 621)
        self.log.prune(self.at + timedelta(days=91))
        self.assertEqual(self.log.sips, [])
        self.assertEqual(self.log.total_for(date(2026, 11, 7)), 155.2)

    def test_round_trip(self):
        self.log.add(self.packet, self.at, 621)
        other = SipLog(LOCAL)
        other.load(self.log.as_dict())
        self.assertEqual(other.sips, self.log.sips)
        self.assertEqual(other.daily, self.log.daily)
        self.assertIsNone(other.add(self.packet, self.at, 621))


if __name__ == "__main__":
    unittest.main(verbosity=1)
