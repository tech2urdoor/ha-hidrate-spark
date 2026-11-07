"""HidrateSpark bottle protocol: UUIDs, packet parsing and command bytes.

Nothing in here touches Home Assistant or Bluetooth, so every byte can be pinned by a test.
Worked out for interoperability, so a bottle can be used without the app: see docs/protocol.md.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

SERVICE = "45855422-6565-4cd7-a2a9-fe8af41b85e8"

FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"
SIPS = "016e11b1-6c8a-4074-9e5a-076053f93784"
COMMANDS = "e3578b0d-caa7-46d6-b7c2-7331c08de044"
SCHEDULE = "b44b03f0-b850-4090-86eb-72863fb3618d"
SETTINGS = "31fb5b6c-0166-4c97-ba1d-bf0a82fbbcb6"
CAPACITY = "316c4914-1f59-462e-af06-185418674c0c"
GLOW_NOW = "a1d9a5bf-f5d8-49f3-a440-e6bf27440cb0"
RUNNING_TOTAL = "6ac24e8b-056c-4077-8221-8f816ade71e6"
FIRMWARE_UPDATE = "00060001-f8ce-11e4-abf4-0002a5d5c51b"

# Writing to these can clear the bottle's total, change its size, or start a firmware update.
# Nothing in this integration writes to them, and a test holds it to that.
NEVER_WRITE = frozenset({CAPACITY, RUNNING_TOTAL, FIRMWARE_UPDATE})

LIGHTS = {
    "high": 0xB4,
    "medium": 0xB0,
    "low": 0xAC,
    "pulse": 0x00,
    "pulse_white": 0x01,
    "effect_pulse": 0x2C,
    "effect_spin": 0x30,
    "effect_flash": 0x34,
}
GOAL_STROBE = 61

# The two RGB565 values SETTINGS holds after the bottle's size. Colour A 0x0001 is a special value.
GLOW_COLOURS = {
    "Rainbow": (0x0001, 0x03BF),
    "Blue": (0x077F, 0x03BF),
    "Red": (0xFC08, 0xF800),
    "Gold": (0xFE20, 0xFFA0),
    "Lime": (0xB7EB, 0x2F8E),
    "Aqua": (0x07FF, 0x07E1),
    "Purple": (0x841F, 0xA81F),
    "White": (0xCE9B, 0xFFFF),
}
SETTINGS_LENGTH = 6

SECONDS_IN_DAY = 86400
SCHEDULE_SLOTS = 12


@dataclass(frozen=True)
class Firmware:
    """The firmware string. Its first number is the bootloader version, which picks the hardware."""

    major: int
    minor: int
    raw: str

    @classmethod
    def parse(cls, data: bytes) -> Firmware:
        raw = bytes(data).split(b"\0", 1)[0].decode("ascii", "replace").strip()
        parts = raw.split(".")
        try:
            return cls(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0, raw)
        except ValueError as err:
            raise ValueError(f"Unreadable firmware version {raw!r}") from err

    @property
    def is_telink(self) -> bool:
        return 100 <= self.major < 111

    @property
    def is_nordic(self) -> bool:
        return 80 <= self.major < 100

    @property
    def supported(self) -> bool:
        """Kinds that need no per-sip acknowledgement and no correction curve.

        The older kinds and Telink work differently. Sips read off a bottle can't be read again, so
        anything else is refused before the first sip rather than decoded on a guess.
        """
        return 40 <= self.major < 100

    @property
    def can_set_colour(self) -> bool:
        """The only family whose SETTINGS layout has been seen. Others may hold something else there."""
        return 50 <= self.major < 60


def colour_settings(current: bytes, colour: str) -> bytes:
    """SETTINGS with only the colours replaced. Refuses a layout it doesn't recognise."""
    current = bytes(current)
    if len(current) != SETTINGS_LENGTH:
        raise ValueError(f"SETTINGS is {len(current)} bytes, expected {SETTINGS_LENGTH}")
    return current[:2] + struct.pack("<HH", *GLOW_COLOURS[colour])


def history_command(firmware: Firmware) -> int:
    """Asks for the next sip. Older bottles and Telink use 0x55."""
    old = firmware.major < 30 and not (firmware.major == 7 and firmware.minor >= 24)
    return 0x55 if firmware.is_telink or old else 0x57


@dataclass(frozen=True)
class Packet:
    """One notification from SIPS."""

    remaining: int
    complete: bool = False
    unused: int = 0
    seconds_ago: int = 0
    low: int = 0
    high: int = 0
    start: int = 0
    stop: int = 0

    @property
    def raw_key(self) -> tuple[int, int, int, int]:
        return (self.low, self.high, self.start, self.stop)


def parse_packet(data: bytes) -> Packet:
    """A packet under 16 bytes carries only the count of sips still on the bottle."""
    data = bytes(data)
    if len(data) < 16:
        return Packet(remaining=data[0] if data else 0)
    remaining, unused, seconds, low, high, start, stop = struct.unpack_from("<BxHiHHHH", data)
    return Packet(remaining, True, unused, seconds, low, high, start, stop)


def level_share(level: int, low: int, high: int) -> float:
    """How full a sensor level means the bottle is, from 0 to 1. A flat sensor range reads as 0."""
    span = max(0, high - min(high, low))
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (level - min(level, low)) / span))


def amount_ml(packet: Packet, size_ml: float) -> float:
    """Millilitres drunk: the fall in level between the sip's start and stop, as a share of the bottle."""
    start = level_share(packet.start, packet.low, packet.high)
    stop = level_share(packet.stop, packet.low, packet.high)
    amount = (start - stop) * size_ml
    return 0.0 if math.isnan(amount) else amount


def is_empty(packet: Packet, size_ml: float) -> bool:
    """The bottle sends an all-zero record when it has nothing to hand over."""
    return amount_ml(packet, size_ml) == 0 and packet.unused == 0 and packet.seconds_ago == 0


def _two_bytes(value: int) -> bytes:
    return bytes(((value // 256) % 256, value % 256))


def day_total_command(total_ml: float, firmware: Firmware) -> bytes:
    """Tells the bottle today's total, in hundredths of a bottle, so it knows if you're behind."""
    bottle = 710 if firmware.major <= 7 else 592
    return b"\x21" + _two_bytes(int(total_ml * 100 / bottle))


def goal_command(goal_ml: float, size_ml: float) -> bytes:
    return b"\x22" + _two_bytes(int(goal_ml * 100 / size_ml))


def goal_glow_command(enabled: bool) -> bytes:
    return bytes((0x93, GOAL_STROBE)) if enabled else b"\x92"


def sip_glow_command(enabled: bool) -> bytes:
    return b"\xb1" if enabled else b"\xb0"


def clock_command(seconds_since_midnight: int) -> bytes:
    return b"\x77\x00\x00\x00" + struct.pack("<I", seconds_since_midnight)


def calibrate_empty_command(firmware: Firmware) -> bytes:
    return bytes((64 if firmware.major >= 50 else 160,))


def calibrate_full_command() -> bytes:
    return b"\x41"


def find_command(light: int) -> bytes:
    return bytes((light & 0xFF,))


@dataclass(frozen=True)
class GlowSettings:
    """What the reminders need. The bottle keeps only the rows, so these live on our side."""

    goal_ml: float
    wake_s: int
    sleep_s: int
    frequency_min: int = 60
    light: int = LIGHTS["high"]
    always: bool = False
    on_sip: bool = True
    on_goal: bool = True


def schedule_rows(
    glow: GlowSettings, firmware: Firmware, size_ml: float, now_s: int
) -> list[bytes]:
    """The glow schedule: reminder times, each with the share of the bottle you should have drunk by then."""
    interval = glow.frequency_min * 60
    count = ((glow.sleep_s - glow.wake_s) % SECONDS_IN_DAY) // interval if interval else 0
    modern = firmware.is_nordic or firmware.is_telink or firmware.minor > 7
    rows = []
    for slot in range(SCHEDULE_SLOTS):
        if slot < count:
            percent = glow.goal_ml * (slot + 1) / (count + 1) / size_ml * 100
            second = (glow.wake_s + (slot + 1) * interval) % SECONDS_IN_DAY
        else:
            percent = 0.0
            second = 0
        modulus = 255 if modern else 256
        low = int(percent % modulus) & 0xFF
        high = int((percent / 256) % modulus) & 0xFF
        if glow.always:
            low = high = 0xFF
        if second == 0:
            low = high = 0
        second_field = glow.light & 0xFF if modern else (1 if now_s > second else 0)
        rows.append(bytes((slot, second_field, low, high)) + struct.pack("<I", second))
    return rows


def reminder_writes(
    glow: GlowSettings, firmware: Firmware, size_ml: float, total_ml: float, now_s: int
) -> list[tuple[str, bytes]]:
    """Every write the reminders need, in the order the bottle expects them on each connection."""
    writes = [
        (COMMANDS, day_total_command(total_ml, firmware)),
        (SCHEDULE, goal_glow_command(glow.on_goal)),
        (COMMANDS, goal_command(glow.goal_ml, size_ml)),
    ]
    if not glow.on_goal or total_ml <= glow.goal_ml:
        writes.append((SCHEDULE, clock_command(now_s)))
    writes.extend((SCHEDULE, row) for row in schedule_rows(glow, firmware, size_ml, now_s))
    writes.append((COMMANDS, sip_glow_command(glow.on_sip)))
    return writes
