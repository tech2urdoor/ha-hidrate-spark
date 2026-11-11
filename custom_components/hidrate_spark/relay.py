"""What a phone is told, and what it sends back, when it reads the bottle away from home.

The phone carries bytes and nothing more: this side says what to ask the bottle for, works out the
sips, and says what to write afterwards. No Home Assistant or Bluetooth in here, so it can be tested
alone. The messages are in docs/phone-relay.md.
"""
from __future__ import annotations

from typing import Any

from .protocol import (
    COMMANDS,
    GLOW_NOW,
    SCHEDULE,
    Firmware,
    Packet,
    acknowledge_command,
    find_command,
    history_command,
    parse_packet,
)
from .sip_log import KEEP_SIPS_DAYS

IDLE_SECONDS = 10
MAX_RECORDS = 500
# The longest record seen is 20 bytes. Anything far past that isn't one.
MAX_RECORD_BYTES = 64
# A phone's clock can run a little ahead. Further ahead than this and its times can't be trusted.
CLOCK_AHEAD_SECONDS = 300
# The only places a phone is ever told to write, beyond asking for the sips.
RELAY_WRITES = frozenset({COMMANDS, SCHEDULE})


class BadRequest(ValueError):
    """The phone sent something that can't be used. Nothing was saved from it.

    `record` is the place in the list of the one record that's at fault, when it's the record itself
    and sending it again could never work. The phone can then put that one aside and keep the rest.
    """

    def __init__(self, reason: str, record: int | None = None) -> None:
        super().__init__(reason)
        self.record = record


def firmware_from(body: dict[str, Any]) -> Firmware:
    raw = body.get("firmware")
    if not isinstance(raw, str) or not raw or len(raw) > 32:
        raise BadRequest("firmware is missing")
    try:
        return Firmware.parse(raw.encode("ascii", "replace"))
    except ValueError as err:
        raise BadRequest(str(err)) from err


def instructions(firmware: Firmware) -> dict[str, Any]:
    """How to take the sips off a bottle with this firmware, or that it mustn't be tried."""
    if not firmware.supported:
        return {"go": False, "reason": "unsupported"}
    return {
        "go": True,
        "ask": bytes((history_command(firmware),)).hex(),
        "acknowledge": acknowledge_command().hex() if firmware.acknowledges_each_sip else None,
        "idle_seconds": IDLE_SECONDS,
    }


def records_from(body: dict[str, Any], now: float) -> list[tuple[Packet, float]]:
    """Each record with the time the phone received it. All of them are checked before any is used."""
    rows = body.get("records", [])
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise BadRequest("records isn't a list of a sensible length")
    records = []
    oldest = now - KEEP_SIPS_DAYS * 86400
    for place, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BadRequest("a record isn't an object", place)
        received = row.get("received")
        if isinstance(received, bool) or not isinstance(received, (int, float)):
            raise BadRequest("a record has no time", place)
        if not received <= now + CLOCK_AHEAD_SECONDS:
            # Not this record's fault: it may be fine once the phone's clock is right, so it isn't pointed at.
            raise BadRequest("a record's time is in the future, so check the phone's clock")
        try:
            data = bytes.fromhex(row.get("hex", ""))
        except (TypeError, ValueError) as err:
            raise BadRequest("a record isn't hex", place) from err
        if not data or len(data) > MAX_RECORD_BYTES:
            raise BadRequest("a record is the wrong length", place)
        packet = parse_packet(data)
        # A sip older than the log keeps would be added to its day and forgotten at once, and then
        # nothing could tell a second copy of it from a new sip.
        if received - max(0, packet.seconds_ago) < oldest:
            raise BadRequest("a record is from too long ago to place", place)
        records.append((packet, float(received)))
    return records


def battery_from(body: dict[str, Any]) -> int | None:
    battery = body.get("battery")
    if isinstance(battery, bool) or not isinstance(battery, int):
        return None
    return battery if 0 <= battery <= 100 else None


def status_from(body: dict[str, Any]) -> bytes | None:
    """A read of COMMANDS, which says whether the bottle is charging."""
    try:
        return bytes.fromhex(body.get("commands") or "")[:MAX_RECORD_BYTES] or None
    except (TypeError, ValueError):
        return None


def find_reply(light: int) -> dict[str, str]:
    """The one write that makes the bottle glow now, in the style picked in Configure.

    Sent with every check, so the phone can still make the bottle glow when Home Assistant can't be
    reached. The phone writes it only when someone presses Find, and only to GLOW_NOW.
    """
    return {"characteristic": GLOW_NOW, "hex": find_command(light).hex()}


def writes_reply(writes: list[tuple[str, bytes]]) -> list[dict[str, str]]:
    """The reminder writes, in order. Refuses to pass on a write to anywhere else."""
    for uuid, _payload in writes:
        if uuid not in RELAY_WRITES:
            raise ValueError(f"{uuid} isn't somewhere a phone is asked to write")
    return [{"characteristic": uuid, "hex": payload.hex()} for uuid, payload in writes]
