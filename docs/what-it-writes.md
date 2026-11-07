# What this integration writes to your bottle

A bottle is a small device with a firmware you can't easily replace, so here is every write, in
plain language. If a change adds a write, it changes this page too. A test in
`tests/test_hidrate_spark.py` checks which characteristics the code writes to.

The names in capitals are this project's labels for the bottle's Bluetooth characteristics. The
UUIDs and byte layouts are in [protocol.md](protocol.md).

## Every sync

| Where | What | Why |
|---|---|---|
| SIPS | Subscribes to notifications, for the length of the read | That's how Bluetooth delivers the sips |
| SIPS | One byte meaning "give me the sips", sent again if the bottle says more are waiting | The bottle only hands over sips when asked |

Nothing else is written unless you turn reminders on. This is still a real change to the bottle: a sip
handed over is gone from it. On the one bottle tested, a second read straight after a sip got
nothing. Each sip is saved in Home Assistant the moment it arrives.

## Every sync, only if you turn on "Send glow reminders to the bottle"

Reminders are off until you turn them on, because these writes replace whatever the bottle held,
including the schedule the app last gave it. **They haven't been shown to work yet.** The bottle
tested reads its schedule back as zeros, so the only proof is watching it glow. They go in this order:

| Where | What | Why |
|---|---|---|
| COMMANDS | Today's total | So the bottle knows whether you're behind |
| SCHEDULE | Goal glow on or off | Your "glow when the goal is reached" setting |
| COMMANDS | Your daily goal | So the bottle knows when you've reached it |
| SCHEDULE | Seconds since midnight | The bottle has no clock of its own. Skipped once the goal is met, if the goal glow is on |
| SCHEDULE | 12 schedule rows | Each row is a reminder time, the share of a bottle you should have drunk by then, and the glow to use. Unused rows are blank |
| COMMANDS | Sip glow on or off | Your "glow on each sip" setting |

## Only when you press a button

| Button | Where | What |
|---|---|---|
| Find | GLOW_NOW | One byte that makes the bottle glow, using your glow setting |
| Calibrate empty | COMMANDS | One byte telling the bottle it is empty now |
| Calibrate full | COMMANDS | One byte telling the bottle it is full now |

Both calibrate buttons are disabled by default, because a stray press would throw off every sip
until you calibrate again. Turn them on from the entity's settings when you need them.

## The probe

`hidrate_spark.probe` writes nothing and takes no sips. It only reads.

## What it never writes

These four are in `NEVER_WRITE` in `protocol.py`. Nothing in this integration writes to them.

| Characteristic | Why not |
|---|---|
| SETTINGS | The bottle's own settings. A write could change its colour or how it behaves |
| CAPACITY | The size the bottle believes it is. Size is an option on the Home Assistant side instead |
| RUNNING_TOTAL | The bottle's own running total. A write could clear it |
| FIRMWARE_UPDATE | The way into a firmware update |

**No firmware updates, ever.** This integration has no firmware to offer and no code to send one.
If your bottle needs an update, that's a job for the official app.

The probe does read SETTINGS, CAPACITY and RUNNING_TOTAL, because reading is safe and shows how a
bottle differs. It does not touch FIRMWARE_UPDATE at all.
