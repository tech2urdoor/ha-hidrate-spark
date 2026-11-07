<img src="custom_components/hidrate_spark/brand/icon.png" alt="" width="96" align="right">

# HidrateSpark Bottle for Home Assistant

Use your HidrateSpark smart bottle from Home Assistant, over Bluetooth, with no app, account or
cloud. It's a community project and is [not affiliated with Hidrate](#disclaimer).

> [!WARNING]
> **Status: early, and proven on one bottle only.** On a Steel with firmware 53.63 it connects
> through a Bluetooth proxy with no pairing, and it has read a real sip with the right time and a
> plausible amount. Glow reminders, Find and calibration haven't been shown to work on any bottle.
> No other bottle has been tried. Read
> [what it writes to your bottle](docs/what-it-writes.md) before you install it.

The point of publishing it is so other owners can report their bottles and get them working. If
yours isn't in the table below, [please report it](CONTRIBUTING.md#report-your-bottle).

## What it replaces

| From the app | Here |
|---|---|
| Sips | `Last sip` sensor with the amount. Every sip is kept in Home Assistant for 90 days |
| Daily totals | `Today` and `Yesterday` sensors. Totals are kept for good, and `Today` carries the last 14 days in its `daily_totals` attribute |
| Daily goal | `Goal progress` sensor |
| Glow reminders | Optional, and off until you turn them on. The schedule is built from your goal, wake time, bedtime and frequency |
| Battery | `Battery` sensor |
| Find my bottle | `Find` button |
| Calibration | `Calibrate empty` and `Calibrate full` buttons, disabled by default so they can't be pressed by accident |

There's also a `Sync now` button and a `Last sync` sensor, whose `last_error` attribute tells you
what went wrong if a sync failed.

## What doesn't carry over

- **App history.** Nothing already in your Hidrate account comes across. Your record starts with
  what's on the bottle when you first connect.
- **Snooze.** There's no way to snooze a reminder.
- **Health Connect.** Sips aren't written to Health Connect or Apple Health. They live in Home
  Assistant, and you can feed them onward from there.
- **Firmware updates.** This integration will [never do these](docs/what-it-writes.md#what-it-never-writes).

## Things to know before you switch

**Use this or the app, not both.** The bottle takes one connection at a time, and a sip is handed
over only once. If the app is running too, each one ends up with part of your day, and
it can block this integration from connecting at all. Close the app while you try this, and
uninstall it when you're happy.

**Sips wait on the bottle until it's home.** There's no phone in your pocket doing the syncing any
more, so nothing is read while the bottle is out of range of Home Assistant. Sips taken away stay on
the bottle. When it comes home they land on the day you drank them, because each one records how
long ago it happened. How many a bottle can hold isn't known yet. A sip that comes home late can't
change what `Today` recorded on the day you drank it, so use `daily_totals` for history.

**Set your bottle's size.** Sip amounts are calculated from it. The default is 621 mL, which is only
right for one model.

**Watch the battery for the first week.** It syncs every 30 minutes by default. You can change that
in the options.

## Requirements

- Home Assistant 2025.1 or newer, with the Bluetooth integration set up. It has only been run on
  2026.10, so older versions are untested. The icon needs 2026.3.
- A Bluetooth adapter, or a [Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html) that
  allows active connections, in range of where the bottle usually sits. A passive-only proxy can't
  read it, because the bottle's adverts carry no readings.

## Install

### With HACS

1. In HACS, open the menu at the top right and choose **Custom repositories**.
2. Add `https://github.com/tech2urdoor/ha-hidrate-spark` with the type **Integration**.
3. Find **HidrateSpark Bottle** in HACS, download it, and restart Home Assistant.

### By hand

Copy `custom_components/hidrate_spark` into your Home Assistant `custom_components` folder, then
restart.

### Then

1. Close the Hidrate app, or the bottle won't be free.
2. Once Home Assistant hears the bottle, it should offer it under **Settings → Devices & services**.
   If it doesn't, choose **Add integration** and search for HidrateSpark.
3. Open the integration's **Configure** screen, and set your bottle's size.
4. Press **Sync now**, then check that `Last sync` updates.

## Options

| Option | Default | |
|---|---|---|
| Send glow reminders to the bottle | Off | Off means the bottle is only read. Turning it on replaces the bottle's schedule |
| Daily goal | 2000 mL | |
| Wake time, Bedtime | 07:00, 22:00 | Reminders are spaced between these |
| Minutes between reminders | 60 | 0 for none. The bottle holds 12 at most |
| Glow | High | High, medium, low, short pulse, short white pulse |
| Glow at every reminder | Off | Otherwise only when you're behind |
| Glow on each sip | On | |
| Glow when the goal is reached | On | |
| Bottle size | 621 mL | |
| Minutes between syncs | 30 | More often is kinder to reminders and harder on the battery |

## Bottle compatibility

**Works** means seen working on a real bottle. **Untested** means written from the protocol and never
run on a real bottle. **Refused** means the integration stops before reading a sip.

| Bottle | Firmware | Connects | Sips | Reminders | Battery | Find | Calibrate |
|---|---|---|---|---|---|---|---|
| Steel, 621 mL | 53.63 | Works | Works, one real sip so far | Untested | Reads a value, accuracy unchecked | Untested | Untested |
| Any other, firmware 40–99 | | Untested | Untested | Untested | Untested | Untested | Untested |
| Firmware below 40 | | Untested | Refused | Refused | Refused | Untested | Untested |
| Firmware 100 and up | | Untested | Refused | Refused | Refused | Untested | Untested |

The bottle is called a Steel because of how the app treats its firmware number. Reminders on it
can't be checked by reading them back, so they stay untested until someone has watched the bottle
glow on time.

The range 40–99 comes from reading the protocol. Only 53.x has been run. Yours can change that:
[report your bottle](CONTRIBUTING.md#report-your-bottle).

To find your firmware version, run the probe below. You don't need to read any sips.

## The probe

**Developer Tools → Actions → `hidrate_spark.probe`** gives a read-only look at the bottle. It takes
no sips and writes nothing. Its output removes your serial number and Bluetooth addresses, so it's
safe to paste into an issue. If you have more than one bottle, it only looks at the first.

## Privacy

Everything stays on your Home Assistant. This integration talks to no server, needs no account and
sends no telemetry. Sips are stored in Home Assistant's `.storage` folder, and your backups will
include them.

## Docs

- [What it writes to your bottle](docs/what-it-writes.md), and what it never writes
- [The bottle's protocol](docs/protocol.md)
- [Contributing](CONTRIBUTING.md)

## Disclaimer

This project is not affiliated with, endorsed by, or supported by Hidrate, Inc. HidrateSpark is
their trademark, used here only to say which bottles this works with. No Hidrate code, text or
artwork is included. The protocol was worked out for interoperability.

It's offered as is, under the [MIT licence](LICENSE), with no warranty. It writes to a device you
own, and that could affect your bottle or its warranty. It isn't a medical device, so don't rely on
it for anything medical.
