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
| SIPS | `0x33` after each sip, on firmware below 30 only, and only once the sip is saved | Those bottles wait for it before sending the next sip |

Nothing else is written unless you turn reminders on. This is still a real change to the bottle: a sip
handed over is gone from it. On both bottles tested, a second read straight after a sip got
nothing. Each sip is saved in Home Assistant the moment it arrives.

## Every sync, only if you turn on "Send glow reminders to the bottle"

Reminders are off until you turn them on, because these writes replace whatever the bottle held,
including the schedule the app last gave it. The bottle tested reads its schedule back as zeros, so
the only proof is watching it glow, which has been seen on one bottle. They go in this order:

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

`Mark empty` and `Mark full` aren't in this table because they write nothing to the bottle. They read WEIGHT and
remember the number in Home Assistant.

Both calibrate buttons are disabled by default, because a stray press would throw off every sip
until you calibrate again. Turn them on from the entity's settings when you need them.

## Only when you change the glow colour

| Control | Where | What |
|---|---|---|
| Glow colour | SETTINGS | Firmware 50 to 59. The bottle's six setting bytes, with the last four replaced by the two colour values |
| Glow colour | GLOW_COLOUR | Firmware 80 to 99. Four bytes, the two colour values and nothing else |

**This changes the bottle's own settings, at your own risk.** It comes with no warranty, like the
rest of this project. It's disabled by default, and a sync never reads or writes SETTINGS or GLOW_COLOUR.
On firmware 50 to 59 it reads SETTINGS first and refuses unless there are exactly six bytes, keeps
the first two as they were, then reads them back to check. On 80 to 99 it reads GLOW_COLOUR first,
refuses unless there are exactly four bytes, writes the two colours, then reads them back to check.
It refuses every other firmware. Only the 50 to 59 layout has been seen working. A 90.11 that was
never set up with the app read no colour at all.

## The probe

`hidrate_spark.probe` writes nothing and takes no sips. It only reads. If you set `weight_seconds`, it also turns on WEIGHT notifications while it listens, which is a flag on the Bluetooth connection, and turns them off again.

## What it never writes

These four are in `NEVER_WRITE` in `protocol.py`. Nothing in this integration writes to them.

| Characteristic | Why not |
|---|---|
| CAPACITY | The size the bottle believes it is. Size is an option on the Home Assistant side instead |
| RUNNING_TOTAL | The bottle's own running total. A write could clear it |
| FIRMWARE_UPDATE | The way into a firmware update |
| FIRMWARE_UPDATE_SMP | The standard Zephyr way into a firmware update. *Seen:* firmware 90.11 has this instead of FIRMWARE_UPDATE |

**No firmware updates, ever.** This integration has no firmware to offer and no code to send one.
If your bottle needs an update, that's a job for the official app.

The probe does read SETTINGS, CAPACITY, RUNNING_TOTAL, WEIGHT and GLOW_COLOUR, because reading is safe and shows
how a bottle differs. It does not touch either firmware update channel at all.
