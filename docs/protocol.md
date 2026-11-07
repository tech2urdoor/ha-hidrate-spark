# The bottle's Bluetooth protocol

What's known about how a HidrateSpark bottle talks over Bluetooth Low Energy, written up so other
owners can check it against their own bottles. `custom_components/hidrate_spark/protocol.py` is the
authority on the bytes. If this page and that file disagree, the file wins and this page has a bug.

**How this was worked out.** By studying a copy of the Android app and a bottle owned by the author,
for the sole purpose of making that bottle work without the app. These are facts about how the
bottle communicates. This repository holds no code, text or artwork from Hidrate.

**How far to trust it.** One bottle has been run so far, with firmware 53.63. Points marked *Seen*
were observed on it. Everything else comes from reading how the app behaves, and could be wrong for
your bottle. The [compatibility table](../README.md#bottle-compatibility) has the overall picture.

The names in capitals are this project's own labels, or Bluetooth's standard names.

Earlier public work that covers parts of this:
[agua_amiga](https://github.com/mpnordland/agua_amiga) and
[wban-python](https://github.com/choonkiatlee/wban-python).

## Finding the bottle

- It advertises the service `45855422-6565-4cd7-a2a9-fe8af41b85e8`, under a name made of `h2o` and
  four hex digits.
- Its adverts carry no manufacturer or service data, so nothing can be learned by listening. Every
  reading needs a real connection.
- One bottle has been seen advertising a second identity at about the same moments: a different
  address, with the same service and the same name in capitals. It's unexplained. The integration
  matches only the lower-case name.

## Connecting

- The bottle takes one connection at a time. While a phone holds it, it stops advertising and a
  proxy can't reach it.
- *Seen:* it needs no pairing or bonding. A Bluetooth proxy connects straight to it.

## Characteristics

| Label here | UUID | Used for | This integration |
|---|---|---|---|
| FIRMWARE_REVISION | `00002a26-0000-1000-8000-00805f9b34fb` | Firmware version | Reads |
| SERIAL_NUMBER | `00002a25-0000-1000-8000-00805f9b34fb` | Serial number | Reads |
| BATTERY_LEVEL | `00002a19-0000-1000-8000-00805f9b34fb` | Battery percent | Reads |
| HARDWARE_REVISION | `00002a27-0000-1000-8000-00805f9b34fb` | Standard hardware revision slot. *Seen:* absent on 53.63 | Reads in the probe |
| SIPS | `016e11b1-6c8a-4074-9e5a-076053f93784` | Handing over sips | Notifications and writes |
| COMMANDS | `e3578b0d-caa7-46d6-b7c2-7331c08de044` | Commands: totals, goal, calibration | Writes, and reads in the probe |
| SCHEDULE | `b44b03f0-b850-4090-86eb-72863fb3618d` | Commands: glow schedule, clock. *Seen:* reads back as zeros on 53.63 | Writes, and reads in the probe |
| GLOW_NOW | `a1d9a5bf-f5d8-49f3-a440-e6bf27440cb0` | Make it glow now | Writes |
| SETTINGS | `31fb5b6c-0166-4c97-ba1d-bf0a82fbbcb6` | Bottle settings. *Seen:* on 53.63 its first two bytes, little-endian, are the bottle's size in mL | Reads in the probe. Writes only when you change the glow colour |
| CAPACITY | `316c4914-1f59-462e-af06-185418674c0c` | Size. *Seen:* absent on 53.63 | Reads in the probe, **never writes** |
| RUNNING_TOTAL | `6ac24e8b-056c-4077-8221-8f816ade71e6` | Running total | Reads in the probe, **never writes** |
| FIRMWARE_UPDATE | `00060001-f8ce-11e4-abf4-0002a5d5c51b` | Firmware updates | **Never touches** |

## Firmware decides the details

The firmware string looks like `53.63`. The first number is the bootloader version, and it picks the
hardware family.

| Major version | Family | This integration |
|---|---|---|
| Below 40 | Older bottles, which work differently | Refuses before reading a sip |
| 40 to 79 | Believed to be Cypress chips. The app treats 50 to 59 as Steel bottles | Attempts it. Only 53.63 has been run |
| 80 to 99 | Nordic chips | Attempts it |
| 100 to 110 | Telink chips, which work differently | Refuses before reading a sip |
| 111 and up | Unknown | Refuses before reading a sip |

The app treats a Steel differently only when reading and writing SETTINGS, which this integration
writes only to change the glow colour, so the reminder bytes are the same.

The refusal is deliberate. Sips are one-shot, so decoding them on a guess could lose them.

## Reading sips

1. Read FIRMWARE_REVISION.
2. Subscribe to notifications on SIPS.
3. Write one byte to SIPS: `0x57` on firmware this integration supports. Telink bottles, and
   older ones below major version 30, use `0x55`. The exception is 7.x from minor version 24, which
   uses `0x57`.
4. Each sip arrives as a notification, with no acknowledgement needed. If a packet carries no sip
   and says more are waiting, write the byte again.
5. Stop when a packet with no sip says none are left, or when nothing arrives for ten seconds. This
   integration also gives up after three minutes.

*Seen:* on 53.63 a real sip arrived with the right time and a plausible amount, and a second sync
straight after got nothing. So a sip is gone from the bottle once it's been read.

Older and Telink bottles also expect `0x33` after each sip, and older ones need a correction curve
on the amount. Neither is implemented, which is part of why they're refused.

### The notification

Little-endian, 16 bytes:

| Bytes | Type | Meaning |
|---|---|---|
| 0 | u8 | Sips still on the bottle. A packet shorter than 16 bytes carries only this |
| 1 | | Unused here |
| 2–3 | u16 | Not part of the amount. Only checked for zero, to spot an empty record |
| 4–7 | i32 | Seconds since the sip. The bottle needs no clock: sip time is now minus this |
| 8–9 | u16 | Sensor minimum |
| 10–11 | u16 | Sensor maximum |
| 12–13 | u16 | Level when the sip started |
| 14–15 | u16 | Level when the sip stopped |

### Turning a sip into millilitres

```
fraction(level) = clamp((level - minimum) / (maximum - minimum), 0, 1)
amount = (fraction(start) - fraction(stop)) * bottle size
```

- If the sensor range is flat, the amount is zero.
- A negative amount means the level rose, which is a refill, and it isn't counted as a sip.
  *Seen:* on 53.63 a refill added no sip in the syncs after it.
- A record with zero amount, bytes 2–3 zero and seconds zero is empty: the bottle has nothing to
  hand over.
- This integration ignores a repeat with identical levels within two minutes of one it already
  holds.
- The bottle's size comes from the integration's options, not the bottle.

## Commands

Multi-byte numbers here are big-endian unless noted. The table lists the commands the integration
sends. See [what-it-writes.md](what-it-writes.md) for when and why.

| Where | Bytes | Meaning |
|---|---|---|
| COMMANDS | `21 hi lo` | Today's total, in hundredths of a reference bottle: 592 mL, or 710 mL on major version 7 and below |
| COMMANDS | `22 hi lo` | Goal, in hundredths of this bottle's size |
| COMMANDS | `b1` / `b0` | Glow on each sip: on / off |
| COMMANDS | `40` | Calibrate empty, major version 50 and up |
| COMMANDS | `a0` | Calibrate empty, below major version 50 |
| COMMANDS | `41` | Calibrate full |
| SCHEDULE | `93 3d` / `92` | Glow when the goal is reached: on, with its strobe value / off |
| SCHEDULE | `77 00 00 00` + u32 little-endian | Seconds since midnight |
| SCHEDULE | 8-byte row, below | One row of the schedule |
| GLOW_NOW | one glow byte, below | Glow now. *Seen:* works on 53.63 |

### Schedule rows

*Seen:* on 53.63 SCHEDULE reads back as zeros, so a schedule can't be checked by reading it. The only
proof is watching the bottle glow. It did: two rows written for minutes nothing else had used fired on
time, about four and nine minutes after the connection ended, with the day's total behind pace. The reminder
flashes three times, spins, and flashes three times again. A sip gives only a brief glow.

A schedule can outlast the device that wrote it, so test with a time no other device has set.

Twelve rows, always. Rows past the last reminder are blank.

| Byte | Meaning |
|---|---|
| 0 | Slot, 0 to 11 |
| 1 | Glow byte on modern bottles (Nordic, Telink, or any minor version above 7). On others, 1 if that time has already passed today, otherwise 0 |
| 2–3 | Share of the bottle you should have drunk by then, as a percentage, low byte then high byte. `ff ff` means glow regardless |
| 4–7 | Second of the day, u32 little-endian. A blank row has zero here and in bytes 2–3 |

The percentage for row `n` of `count` is
`goal * (n + 1) / (count + 1) / bottle size * 100`, and the time is
`wake + (n + 1) * interval`, wrapping past midnight.

### Glow colours

*Seen:* on 53.63 SETTINGS is six bytes: the bottle's size, then colour A and colour B, each a
little-endian u16 in RGB565. A bottle that had never had a colour set read `6d02 0000 0000`. A colour
A of `0x0001` is a special value meaning rainbow. All eight below have been written to a bottle and
watched, and they work.

| Colour | A | B |
|---|---|---|
| Rainbow | `0x0001` | `0x03BF` |
| Blue | `0x077F` | `0x03BF` |
| Red | `0xFC08` | `0xF800` |
| Gold | `0xFE20` | `0xFFA0` |
| Lime | `0xB7EB` | `0x2F8E` |
| Aqua | `0x07FF` | `0x07E1` |
| Purple | `0x841F` | `0xA81F` |
| White | `0xCE9B` | `0xFFFF` |

### Glow bytes

| Glow | Byte |
|---|---|
| High | `b4` |
| Medium | `b0` |
| Low | `ac` |
| Short pulse | `00` |
| Short white pulse | `01` |
| Pulse effect | `2c` |
| Spin effect | `30` |
| Flash effect | `34` |

The three effects haven't been watched on a bottle yet.

## Still unknown

Reports on any of these are very welcome.

- How many sips a bottle holds while it's away from home.
- Whether sips are one-shot on firmware other than 53.63.
- Whether a reminder stays quiet once the day's total is ahead of its row. Only a glow when behind has been seen.
- What makes the bottle glow blue on a refill. *Seen:* on 53.63 it did so with nothing connected, so
  it's the bottle's own firmware and nothing has to ask for it.
- How the second advertised identity relates to the first.
- Which firmware versions each model ships with, and whether the major-version families above hold.
- What the probe's SETTINGS, COMMANDS and RUNNING_TOTAL reads mean, byte by byte, beyond the size above.
- Whether writing a schedule every 30 minutes costs noticeable battery.
