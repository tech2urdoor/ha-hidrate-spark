"""One connection to the bottle, written against a bleak-like client so tests can fake it."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from .protocol import (
    BATTERY_LEVEL,
    CAPACITY,
    COMMANDS,
    FIRMWARE_REVISION,
    GLOW_COLOUR,
    HARDWARE_REVISION,
    RUNNING_TOTAL,
    WEIGHT,
    SCHEDULE,
    SERIAL_NUMBER,
    SETTINGS,
    SIPS,
    Firmware,
    GlowSettings,
    Packet,
    acknowledge_command,
    charge_state,
    colour_settings,
    glow_colour_bytes,
    weight_reading,
    history_command,
    is_empty,
    parse_packet,
    reminder_writes,
)

IDLE_SECONDS = 10.0
READ_LIMIT_SECONDS = 180.0
# Two weight reads this far apart, and this close together, mean the bottle is standing still.
WEIGHT_GAP_SECONDS = 1.0
WEIGHT_STILL = 10

# Characteristics that are safe to read. SIPS isn't here, because it hands over sips, and
# neither firmware update channel is, because nothing here has business touching them.
PROBE_READS = {
    "firmware": FIRMWARE_REVISION,
    "serial": SERIAL_NUMBER,
    "battery": BATTERY_LEVEL,
    "hardware_revision": HARDWARE_REVISION,
    "settings": SETTINGS,
    "capacity": CAPACITY,
    "commands": COMMANDS,
    "schedule": SCHEDULE,
    "running_total": RUNNING_TOTAL,
    "weight": WEIGHT,
    "glow_colour": GLOW_COLOUR,
}


REDACTED = "<redacted>"
_ADDRESS = re.compile(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b")
_BOTTLE_NAME = re.compile(r"\bh2o[0-9A-Fa-f]{4,}\b", re.IGNORECASE)
_SHORTEST_PRIVATE = 4


class UnsupportedBottle(Exception):
    """This firmware works differently, so no sips were taken."""


class ColourNotSet(Exception):
    """The colour was refused before writing, or didn't read back as written."""


@dataclass
class SyncResult:
    firmware: Firmware
    serial: str | None
    battery: int | None
    packets: int
    weight: int | None = None
    charge: str | None = None


async def read_firmware(client: Any) -> Firmware:
    return Firmware.parse(await client.read_gatt_char(FIRMWARE_REVISION))


async def read_sips(
    client: Any,
    firmware: Firmware,
    size_ml: float,
    on_sip: Callable[[Packet, float], None],
    *,
    idle_seconds: float = IDLE_SECONDS,
    limit_seconds: float = READ_LIMIT_SECONDS,
    clock: Callable[[], float] = time.time,
) -> int:
    """Take the sips off the bottle.

    Reads look one-shot, so each sip goes to on_sip the moment it arrives: a dropped connection
    must not lose the ones already handed over. Returns how many sips arrived.
    """
    command = bytes((history_command(firmware),))
    queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_seconds
    count = 0

    def _notified(_sender: Any, data: bytearray) -> None:
        queue.put_nowait((clock(), bytes(data)))

    await client.start_notify(SIPS, _notified)
    try:
        await client.write_gatt_char(SIPS, command)
        while True:
            wait = min(idle_seconds, deadline - loop.time())
            if wait <= 0:
                break
            try:
                received, data = await asyncio.wait_for(queue.get(), wait)
            except TimeoutError:
                break
            packet = parse_packet(data)
            if not packet.complete or is_empty(packet, size_ml):
                if packet.remaining == 0:
                    break
                await client.write_gatt_char(SIPS, command)
                continue
            on_sip(packet, received)
            count += 1
            # Only once the sip is handed over, so a dropped link can't lose one the bottle let go of.
            if firmware.acknowledges_each_sip:
                await client.write_gatt_char(SIPS, acknowledge_command())
    finally:
        try:
            await client.stop_notify(SIPS)
        except Exception:  # noqa: BLE001 - the link may already be gone, and the sips are safe
            pass
    return count


async def _read_battery(client: Any) -> int | None:
    data = bytes(await client.read_gatt_char(BATTERY_LEVEL))
    return data[0] if data and data[0] <= 100 else None


async def _read_serial(client: Any) -> str | None:
    data = bytes(await client.read_gatt_char(SERIAL_NUMBER))
    return data.split(b"\0", 1)[0].decode("ascii", "replace").strip() or None


async def read_weight(client: Any, gap_seconds: float = WEIGHT_GAP_SECONDS) -> int | None:
    """None if it can't be read or the bottle is moving, because a reading in someone's hand isn't a level."""
    try:
        first = weight_reading(bytes(await client.read_gatt_char(WEIGHT)))
        if first is None:
            return None
        await asyncio.sleep(gap_seconds)
        second = weight_reading(bytes(await client.read_gatt_char(WEIGHT)))
    except Exception:  # noqa: BLE001 - a level is a bonus, so it must not cost the rest of a sync
        return None
    return second if second is not None and abs(second - first) <= WEIGHT_STILL else None


async def read_charge(client: Any) -> str | None:
    """Charging, charged or not, from a read of COMMANDS. None if it can't be read."""
    try:
        return charge_state(bytes(await client.read_gatt_char(COMMANDS)))
    except Exception:  # noqa: BLE001 - like the weight, a bonus that must not cost the rest of a sync
        return None


async def sync(
    client: Any,
    *,
    size_ml: float,
    on_sip: Callable[[Packet, float], None],
    total_today: Callable[[], float],
    now_seconds: Callable[[], int],
    glow: GlowSettings | None,
    known_serial: str | None = None,
    idle_seconds: float = IDLE_SECONDS,
    clock: Callable[[], float] = time.time,
    on_firmware: Callable[[Firmware], None] | None = None,
) -> SyncResult:
    """Sips first, because they matter most; then battery, charging and weight; then the reminders."""
    firmware = await read_firmware(client)
    if not firmware.supported:
        raise UnsupportedBottle(f"Firmware {firmware.raw} isn't a kind this integration can read")
    if on_firmware is not None:
        on_firmware(firmware)

    packets = await read_sips(
        client, firmware, size_ml, on_sip, idle_seconds=idle_seconds, clock=clock
    )
    serial = known_serial or await _read_serial(client)
    battery = await _read_battery(client)
    charge = await read_charge(client)
    weight = await read_weight(client)
    if glow is not None:
        for uuid, payload in reminder_writes(glow, firmware, size_ml, total_today(), now_seconds()):
            await client.write_gatt_char(uuid, payload)
    return SyncResult(firmware, serial, battery, packets, weight, charge)


async def set_colour(client: Any, colour: str) -> None:
    """The only write to SETTINGS or GLOW_COLOUR. It runs only when asked for, never during a sync, and takes no sips.

    Nordic bottles keep the colour in GLOW_COLOUR, and 50 to 59 in SETTINGS after the bottle's size.
    """
    firmware = await read_firmware(client)
    if not firmware.can_set_colour:
        raise ColourNotSet(f"Changing the colour hasn't been tried on firmware {firmware.raw}")
    # Each write names its characteristic, so the test of what's written can see it.
    if firmware.is_nordic:
        current = bytes(await client.read_gatt_char(GLOW_COLOUR))
        wanted = _colour_bytes(glow_colour_bytes, current, colour)
        if current != wanted:
            await client.write_gatt_char(GLOW_COLOUR, wanted)
            _check_colour(wanted, await client.read_gatt_char(GLOW_COLOUR))
        return
    current = bytes(await client.read_gatt_char(SETTINGS))
    wanted = _colour_bytes(colour_settings, current, colour)
    if current != wanted:
        await client.write_gatt_char(SETTINGS, wanted)
        _check_colour(wanted, await client.read_gatt_char(SETTINGS))


def _colour_bytes(build: Callable[[bytes, str], bytes], current: bytes, colour: str) -> bytes:
    try:
        return build(current, colour)
    except ValueError as err:
        raise ColourNotSet(str(err)) from err


def _check_colour(wanted: bytes, after: bytes) -> None:
    after = bytes(after)
    if after != wanted:
        raise ColourNotSet(f"The bottle read back {after.hex()} after writing {wanted.hex()}")


def private_values(*texts: str | None, address: str | None = None) -> set[str]:
    """What a report must not show: each text, its hex, and the address in either byte order."""
    values: set[str] = set()
    for text in texts:
        if text and len(text) >= _SHORTEST_PRIVATE:
            values |= {text, text.encode().hex()}
    if address:
        octets = re.findall(r"[0-9a-f]{2}", address.lower())
        values |= {"".join(octets), "".join(reversed(octets))}
    return values


def redact_text(text: str, private: Collection[str] = ()) -> str:
    """Removes anything shaped like an address or a bottle's name, and each private value."""
    text = _BOTTLE_NAME.sub(REDACTED, _ADDRESS.sub(REDACTED, text))
    # Longest first, so a short value can't leave part of a longer one behind.
    for value in sorted(private, key=len, reverse=True):
        text = re.sub(re.escape(value), REDACTED, text, flags=re.IGNORECASE)
    return text


def _redact(value: Any, private: Collection[str]) -> Any:
    if isinstance(value, str):
        return redact_text(value, private)
    if isinstance(value, dict):
        return {key: _redact(item, private) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, private) for item in value]
    return value


def mask_name(name: str | None) -> str | None:
    """Keeps the start of a bottle's name, which discovery needs, and drops the part unique to it."""
    return f"{name[:3]}****" if name else None


async def _listen_to_weight(client: Any, seconds: float) -> dict[str, Any]:
    """Weight readings arrive as notifications, and they're what shows whether a level can be read."""
    heard: list[list[Any]] = []
    started = time.monotonic()

    def on_weight(_sender: Any, data: bytearray) -> None:
        heard.append([round(time.monotonic() - started, 1), bytes(data).hex()])

    try:
        await client.start_notify(WEIGHT, on_weight)
        await asyncio.sleep(seconds)
        await client.stop_notify(WEIGHT)
    except Exception as err:  # noqa: BLE001 - the same as an unreadable characteristic
        return {"error": str(err), "heard": heard}
    return {"seconds": seconds, "heard": heard}


async def probe(
    client: Any, private: Collection[str] = (), weight_seconds: float = 0
) -> dict[str, Any]:
    """Everything that can be learned without taking a sip or writing anything.

    The report is made to be pasted into a public issue. The serial is left out, and it, the
    address and everything in `private` are removed wherever else they turn up.
    """
    hidden = set(private)
    report: dict[str, Any] = {}
    for name, uuid in PROBE_READS.items():
        try:
            raw = bytes(await client.read_gatt_char(uuid))
        except Exception as err:  # noqa: BLE001 - an unreadable characteristic is itself the answer
            report[name] = {"error": str(err)}
            continue
        if uuid == SERIAL_NUMBER:
            serial = raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()
            hidden |= private_values(serial)
            report[name] = {"redacted": True, "length": len(serial)}
            continue
        entry: dict[str, Any] = {"hex": raw.hex()}
        if raw and all(32 <= byte < 127 or byte == 0 for byte in raw):
            entry["text"] = raw.split(b"\0", 1)[0].decode("ascii")
        report[name] = entry
    if weight_seconds > 0:
        report["weight_notifications"] = await _listen_to_weight(client, weight_seconds)
    report["gatt"] = {
        service.uuid: [
            f"{char.uuid} {','.join(char.properties)}" for char in service.characteristics
        ]
        for service in client.services
    }
    return _redact(report, hidden)
