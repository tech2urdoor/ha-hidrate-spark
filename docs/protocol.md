# The bottle's Bluetooth protocol

What's known about how a HidrateSpark bottle talks over Bluetooth Low Energy, written up so other
owners can check it against their own bottles. `custom_components/hidrate_spark/protocol.py` is the
authority on the bytes. If this page and that file disagree, the file wins and this page has a bug.

**How this was worked out.** By studying a copy of the Android app and a bottle owned by the author,
for the sole purpose of making that bottle work without the app. These are facts about how the
bottle communicates. This repository holds no code, text or artwork from Hidrate.

**How far to trust it.** Two bottles have been run. One, with firmware 53.63, has been run for days.
The other, a 946 mL Pro with firmware 90.11, has connected, synced, been probed and calibrated,
and handed over one measured sip. Points marked *Seen* were observed on one of them, and say which. Everything
else comes from reading how the app behaves, and could be wrong for your bottle. The
[compatibility table](../README.md#bottle-compatibility) has the overall picture.

The names in capitals are this project's own labels, or Bluetooth's standard names.

Earlier public work that covers parts of this:
[agua_amiga](https://github.com/mpnordland/agua_amiga) and
[wban-python](https://github.com/choonkiatlee/wban-python). Loryan Strant's
[HidrateSpark-MQTT-bridge](https://github.com/loryanstrant/HidrateSpark-MQTT-bridge) and
[Home Assistant integration](https://github.com/loryanstrant/HA-HidrateSpark-Bluetooth-Proxy) document
the weight characteristic on firmware 80.18. No code from any of them is used here.

## Finding the bottle

- It advertises the service `45855422-6565-4cd7-a2a9-fe8af41b85e8`, under a name made of `h2o` and
  five hex digits. *Seen:* on 90.11 the name in some adverts is padded with zero bytes after the
  digits. Home Assistant still matched it and offered the bottle by itself.
- Its adverts carry no manufacturer or service data, so nothing can be learned by listening. Every
  reading needs a real connection.
- One bottle has been seen advertising a second identity at about the same moments: a different
  address, with the same service and the same name in capitals. It's unexplained. The integration
  matches only the lower-case name.

## Connecting

- The bottle takes one connection at a time. While a phone holds it, it stops advertising and a
  proxy can't reach it.
- *Seen:* it needs no pairing or bonding. A Bluetooth proxy connects straight to it.
- *Seen:* on 90.11 this held for a bottle straight out of the box, never set up with the app or an
  account. It connected, synced and was probed with nothing else ever having touched it.

## Characteristics

| Label here | UUID | Used for | This integration |
|---|---|---|---|
| FIRMWARE_REVISION | `00002a26-0000-1000-8000-00805f9b34fb` | Firmware version | Reads |
| SERIAL_NUMBER | `00002a25-0000-1000-8000-00805f9b34fb` | Serial number | Reads |
| BATTERY_LEVEL | `00002a19-0000-1000-8000-00805f9b34fb` | Battery percent | Reads |
| HARDWARE_REVISION | `00002a27-0000-1000-8000-00805f9b34fb` | Standard hardware revision slot. *Seen:* absent on 53.63, present but empty on 90.11 | Reads in the probe |
| SIPS | `016e11b1-6c8a-4074-9e5a-076053f93784` | Handing over sips | Notifications and writes |
| COMMANDS | `e3578b0d-caa7-46d6-b7c2-7331c08de044` | Commands: totals, goal, calibration. *Seen:* on 90.11 a read returned `88200000` out of the box on the charger, `88000000` after calibrating empty and `80000000` after calibrating full, then `80200000` back on the charger. Believed: byte 0's `08` means the bottle wants calibrating, byte 1's `20` charging and its `10` charged. Byte 0 of exactly `80` is the bottle at rest. That fits what was seen: `08` cleared with calibration, and `20` showed only on the charger. Nothing in it marks a setup or demo state. The rest isn't known | Writes, and reads in the probe |
| SCHEDULE | `b44b03f0-b850-4090-86eb-72863fb3618d` | Commands: glow schedule, clock. *Seen:* reads back as zeros on 53.63. On 90.11 it's write-only, and a read is refused | Writes, and reads in the probe |
| GLOW_NOW | `a1d9a5bf-f5d8-49f3-a440-e6bf27440cb0` | Make it glow now | Writes |
| GLOW_COLOUR | `b810e826-cf05-4b46-a725-07bc0fa2e5d9` | Glow colour on Nordic bottles, in the same service as GLOW_NOW. Believed to be four bytes: a preset number then three zeros, or two 16-bit little-endian colours. *Seen:* present on 90.11, readable and writable. A 90.11 never set up with the app read `00000000`, as SETTINGS colours on 53.63 did from the factory | Reads in the probe. Writes only when you change the glow colour, as the two colours below |
| SETTINGS | `31fb5b6c-0166-4c97-ba1d-bf0a82fbbcb6` | Bottle settings. *Seen:* on 53.63 its first two bytes, little-endian, are the bottle's size in mL. Absent on 90.11 | Reads in the probe. Writes only when you change the glow colour |
| CAPACITY | `316c4914-1f59-462e-af06-185418674c0c` | Size. *Seen:* absent on 53.63. On 90.11, straight out of the box, it read six zero bytes | Reads in the probe, **never writes** |
| RUNNING_TOTAL | `6ac24e8b-056c-4077-8221-8f816ade71e6` | Running total. *Seen:* absent on 90.11 | Reads in the probe, **never writes** |
| WEIGHT | `1807a063-4e2d-4636-981a-35e93d1c7b94` | A live weight reading. *Seen on 53.63:* a plain read returns 22 bytes. The first two are one big-endian number that follows the water, and the rest didn't change at any level. *Seen on 90.11:* notifications only, and a plain read is refused. See [the weight value](#the-weight-value) | Read at each sync, and when a mark button is pressed |
| FIRMWARE_UPDATE | `00060001-f8ce-11e4-abf4-0002a5d5c51b` | Firmware updates. *Seen:* absent on 90.11 | **Never touches** |
| FIRMWARE_UPDATE_SMP | `da2e7828-fbce-4e01-ae9e-261174997c48` | Firmware updates through the standard Zephyr update service, `8d53dc1d-1db7-4cd3-868b-8a527460aa84`. *Seen:* on 90.11 | **Never touches** |

## Firmware decides the details

The firmware string looks like `53.63`. The first number is the bootloader version, and it picks the
hardware family.

| Major version | Family | This integration |
|---|---|---|
| Below 8 | The earliest bottles | Refuses before reading a sip |
| 8 to 19 | Older bottles that wait for `0x33` after each sip | Attempts it, and keeps each record whole. Never run |
| 20 to 29 | A different product | Refuses before reading a sip |
| 30 to 39 | Older bottles, read like newer ones | Attempts it, and keeps each record whole. Never run |
| 40 to 79 | Believed to be Cypress chips. The app treats 50 to 59 as Steel bottles | Attempts it. Only 53.63 has been run. Keeps each record whole, except on 50 to 59 |
| 80 to 99 | Nordic chips. The app treats 90 to 99 as its 946 mL Pro | Attempts it, and keeps each record whole. 90.11 has read one measured sip close to right |
| 100 to 110 | Telink chips, which work differently | Refuses before reading a sip |
| 111 and up | Unknown | Refuses before reading a sip |

The app treats a Steel differently only when reading and writing SETTINGS, which this integration
writes only to change the glow colour, so the reminder bytes are the same.

The refusals are deliberate. Sips are one-shot, so decoding them on a guess could lose them. On
every family it attempts except 50 to 59, the only one seen reading sips over days, it saves each
notification exactly as received alongside the sip, so a wrong reading can be redone later from the
saved bytes. That includes a sip that works out to nothing, which would otherwise be dropped, as it
might be on a bottle that has never been calibrated.

## Reading sips

1. Read FIRMWARE_REVISION.
2. Subscribe to notifications on SIPS.
3. Write one byte to SIPS: `0x57` from major version 30 up. Telink bottles, and older ones below
   major version 30, use `0x55`. The exception is 7.x from minor version 24, which uses `0x57`.
4. Each sip arrives as a notification. Below major version 30, and on Telink, write `0x33` to SIPS
   after each sip to ask for the next. This integration writes it only after the sip has been
   handed over to be saved. Newer bottles need no acknowledgement. If a packet carries no sip and
   says more are waiting, write the byte from step 3 again.
5. Stop when a packet with no sip says none are left, or when nothing arrives for ten seconds. This
   integration also gives up after three minutes.

*Seen:* on 53.63 a real sip arrived with the right time and a plausible amount, and a second sync
straight after got nothing. So a sip is gone from the bottle once it's been read. On 90.11, just
calibrated, 250 mL poured out by measuring cup read as 235 mL with the right time, and a second sync
straight after got nothing there too.

Telink bottles are still refused: they're believed to differ in more than the acknowledgement.
Bottles from major version 8 to 39 are believed to have their amounts adjusted by the app near full
and near empty. This integration doesn't do that and uses the straight share below. Public captures
from an older bottle ([wban-python](https://github.com/choonkiatlee/wban-python)) show byte 1
matching that straight share, as a whole percent of the bottle, on two of three sips.

### The notification

Little-endian, 16 bytes:

| Bytes | Type | Meaning |
|---|---|---|
| 0 | u8 | Sips still on the bottle. A packet shorter than 16 bytes carries only this |
| 1 | u8 | Unused here. On captures from an older bottle it was the sip as a whole percent of the bottle |
| 2–3 | u16 | Not part of the amount. Only checked for zero, to spot an empty record |
| 4–7 | i32 | Seconds since the sip. The bottle needs no clock: sip time is now minus this |
| 8–9 | u16 | Sensor minimum |
| 10–11 | u16 | Sensor maximum |
| 12–13 | u16 | Level when the sip started |
| 14–15 | u16 | Level when the sip stopped |

*Seen:* on 90.11 the notification is 20 bytes: these 16, then four zero bytes. Byte 1 was the sip as
a whole percent of the bottle, rounded down, and bytes 2–3 held the same number.

### Turning a sip into millilitres

```
fraction(level) = clamp((level - minimum) / (maximum - minimum), 0, 1)
amount = (fraction(start) - fraction(stop)) * bottle size
```

- If the sensor range is flat, the amount is zero.
- A negative amount means the level rose, which is a refill, and it isn't counted as a sip.
  *Seen:* on 53.63 a refill added no sip in the syncs after it.
- A rise of at least 5% of the bottle counts as a refill, either within a record, or from where one
  sip stopped to where the next started. *Seen:* one refill showed up the second way, a rise of about
  half the bottle, and two sips with no refill between them read a rise of exactly nothing. The first
  way comes from how the amount works out and hasn't been seen. The 5% is a margin, not a measurement.
- How full the bottle is comes from the same sum: `fraction(stop) * bottle size` on the latest record.
- *Seen:* on 53.63 and 90.11 every record has started at the level where the last one stopped, so
  water drunk in a sip that sends no record should count in the next one. Seen once on 90.11: a sip
  glowed but sent no record, and the next sip's record still started where the last one stopped.
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
| COMMANDS | `b1` / `b0` | Glow on each sip: on / off. *Seen:* on 90.11 the sip glow showed only once GLOW_COLOUR held a colour |
| COMMANDS | `40` | Calibrate empty, major version 50 and up |
| COMMANDS | `a0` | Calibrate empty, below major version 50 |
| COMMANDS | `41` | Calibrate full |

*Seen:* on 90.11, straight out of the box, `40` with the bottle empty and then `41` with it full were
both accepted, and the sip after started exactly at the sensor maximum.
| SCHEDULE | `93 3d` / `92` | Glow when the goal is reached: on, with its strobe value / off |
| SCHEDULE | `77 00 00 00` + u32 little-endian | Seconds since midnight |
| SCHEDULE | 8-byte row, below | One row of the schedule |
| GLOW_NOW | one glow byte, below | Glow now. *Seen:* works on 53.63, and on 90.11 once GLOW_COLOUR held a colour |

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

Nordic bottles (80 to 99) keep the same two values in GLOW_COLOUR on their own: four bytes, colour A
then colour B, each a little-endian u16. Aqua is `ff07 e107`. *Seen:* a 90.11 that read `0000 0000`
there showed nothing on Find. Once Aqua had been written and read back, Find glowed.

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

*Seen:* on 53.63 the pulse and spin effects work through GLOW_NOW, and one write plays more than once
by itself. They use the bottle's glow colour. On 53.63 and 90.11 one write of the flash effect plays it
three times in a row. An effect in a schedule row hasn't been watched.

## Still unknown

Reports on any of these are very welcome.

- How many sips a bottle holds while it's away from home.
- Whether sips are one-shot on firmware other than 53.63 and 90.11.
- Whether a bottle that has never been calibrated sends a usable sensor minimum and maximum, and so
  usable amounts. The app offers calibration straight after setup.
- Whether a reminder stays quiet once the day's total is ahead of its row. Only a glow when behind has been seen.
- What makes the bottle glow blue on a refill. *Seen:* on 53.63 it did so with nothing connected, so
  it's the bottle's own firmware and nothing has to ask for it.
- How the second advertised identity relates to the first.
- Which firmware versions each model ships with, and whether the major-version families above hold.
- What the probe's SETTINGS, COMMANDS and RUNNING_TOTAL reads mean, byte by byte, beyond the size above.
- Whether writing a schedule every 30 minutes costs noticeable battery.

## The weight value

Measured on one Steel, firmware 53.63, standing upright, by pouring with a measuring cup:

| Bottle | First two bytes | As a number |
|---|---|---|
| Empty | `88 6a` | 34922 |
| Before pouring | `8a a7` | 35495 |
| 160 mL poured out | `89 c1` | 35265 |
| The 160 mL poured back | `8a a9` | 35497 |
| Full to the line, about 650 mL from empty | `8c 05` | 35845 |

That's about 1.42 to 1.44 units per mL. The notes linked above describe firmware 80.18, where the high byte is a
status (`8a` upright and stable) and the low byte about 1 mL per unit. On 53.63 the high byte changes with the water,
so it can't be a status there. Which firmware does which is unknown, so the integration doesn't assume a scale, and
works from two readings the owner marks.

Straight after coming off the charger the value fell 7 units in 17 minutes with the water untouched, about 5 mL, and
then held. With the base on its charger it read `86 47`, which is outside the range above. Tilted hasn't been tried.

On firmware 90.11 a plain read of WEIGHT is refused, so the integration gets no reading at a sync and `Water left`
comes from sips, and the mark buttons can't get a reading either. Its notifications do arrive: 10 seconds of listening
on its charger heard one, two bytes, `77 74`. Whether that fits the 80.18 notes above is unknown.
