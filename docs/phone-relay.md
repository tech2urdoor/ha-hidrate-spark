# Phone relay: syncing while the bottle is away from home

Home Assistant can only read a bottle that's in range of one of its Bluetooth proxies. The relay is a
small Android app for a phone that stays near the bottle when it's out: it connects to the bottle,
passes what it reads to Home Assistant over the internet, and makes the reminder writes Home Assistant
asks for. It's optional and off by default.

**Seen working on one bottle and one phone:** a Pro on firmware 90.11, away from home, with a Pixel
on Android 17. A sync pressed in the app handed over three sips, which landed in Home Assistant at
the times they were drunk, an hour earlier, and the reminder writes went through. The first
scheduled run, a minute later with the app open, also synced. Checked afterwards in Home
Assistant's store: each of the three was saved once, they sit in an unbroken run with the sips Home
Assistant read itself before and after, and the day's total is their sum. Once the bottle was home
again, Home Assistant read the next two sips itself, with the app's switch left on, and the app's
next run logged that the bottle was at home and left it alone.

**Seen not working: scheduled runs with the screen off.** After those first two runs the app ran
nothing for two and a half hours, where five runs were due, and a sip taken in that time waited
for the bottle to get home. Android had moved the app to its "rare" standby bucket, which holds
background work back for hours. The app now asks to be let off that, with **Allow background
running**. With that allowed, the app's log showed a run about every 30 minutes for five hours, each
one finding the bottle at home and leaving it alone. Whether the screen was off and the phone
unplugged through those wasn't checked, and no scheduled run has yet been seen reading a bottle
away from home. Until one has, count on the relay only when you open the app and press sync.

**Not seen yet:** records delivered late after a lost connection, a record refused and set aside,
a backlog sent in batches, firmware that wants an acknowledgement after each sip, and what it costs
the phone's battery. Everything else rests on tests with a fake bottle.

The phone decides nothing. It doesn't know what a record means, how big your bottle is or what your
goal is. Home Assistant tells it which byte asks for the sips, works out the sips from the raw
records, and says which bytes to write afterwards, exactly as it would on its own connection.

## Setting it up

1. Your Home Assistant has to be reachable from where the phone will be, at an `https` address. The
   app only takes a plain `http` address when it's on a private network, which is what the phone sees
   through a VPN to your home.
2. In Home Assistant, open the bottle's **Configure** and tick **Let a phone read the bottle away from
   home**. The next screen shows the relay address. Anyone who has it can add sips to that bottle, so
   treat it like a password.
3. Build the app from [`android/`](../android) and install it. It needs Android 13 or later.
4. In the app, paste the relay address and save it, choose your bottle, and turn on **Sync every 30
   minutes**. Allow **Nearby devices** when asked. It never asks for your location.
5. Tap **Allow background running** and allow it. Android otherwise holds the 30-minute runs back
   while the screen is off. The line under the switch says whether it's allowed.

One phone relays one bottle. To stop, untick the option: the address stops working at once, and comes
back unchanged if you tick it again.

## What happens on each run

1. Records left from an earlier run are sent first, if any.
2. The phone asks Home Assistant whether to go ahead. If Home Assistant has heard the bottle in the
   last two minutes, the answer is no, and the phone does nothing: at home, Home Assistant reads the
   bottle itself. If Home Assistant can't be reached, the phone also does nothing, and sips wait on the
   bottle as they always have.
3. The phone listens for the bottle for up to 30 seconds, connects, reads the firmware version, and
   asks Home Assistant how to read that firmware. Unsupported firmware stops here, before any sip.
4. It asks the bottle for its sips. Each record is written to the phone's storage the moment it
   arrives, because a bottle hands each sip over only once.
5. It reads the battery level and the status bytes, and sends everything to Home Assistant, 200
   records at a time if there's a long backlog.
6. Home Assistant saves the sips, with the times they were drunk, and replies with the reminder writes
   if you have reminders on. The phone makes them and disconnects.

A record leaves the phone's storage only when Home Assistant confirms it. If the internet drops
part-way, the records go on the next run. Sending the same record twice counts once. A record the
bottle sends after the phone has stopped waiting for sips is kept as well, and goes with the rest or
on the next run.

## What the phone writes to the bottle

The same things a sync from Home Assistant writes, listed in [what-it-writes.md](what-it-writes.md):
the request for sips, the `0x33` after each sip on firmware below 30, and the reminder writes.
The app refuses any write that isn't to COMMANDS or SCHEDULE, whatever a reply says, and the
integration refuses to ask for one. The one exception is **Find bottle**, below: a single byte to
GLOW_NOW, written only when you press it. The app can't change the glow colour or calibrate.

## Find bottle

**Untested on a real bottle.** Pressing **Find bottle** in the app makes the bottle glow, the same
as the `Find` button in Home Assistant:

1. The app asks Home Assistant first. If Home Assistant has heard the bottle in the last two
   minutes, it makes the bottle glow over its own connection and the phone does nothing more. The
   bottle takes one connection at a time, so this keeps the two from fighting over it.
2. Otherwise Home Assistant replies with the byte to write, and the phone connects and writes it to
   GLOW_NOW. If Home Assistant can't be reached within 8 seconds, the phone uses the byte from its
   last sync. With no sync yet, it has nothing to go on and writes nothing.
3. For the next minute, the screen shows how strong the bottle's signal is, in words and in dBm, so
   you can walk towards it. The number jumps around: a body or a wall in the way costs 10 dB or more.

The app refuses any Find instruction that isn't exactly one byte to GLOW_NOW. Bluetooth reaches
about 10 metres, so the phone has to be near the bottle already. It can't find a bottle left
somewhere else. A bottle that has never had a glow colour set may show nothing, as with Find in
Home Assistant.

It never reads WEIGHT, so `Water left` comes from sips while the bottle is away.

## The messages

Every call is a JSON `POST` to the relay address. Byte strings are lower-case hex.

| Step | The phone sends | Home Assistant replies |
|---|---|---|
| `check` | `{"step": "check"}` | `{"go": true, "find": {…}}`, or `{"go": false, "reason": "in_range", "find": {…}}` |
| `find` | `{"step": "find"}` | `{"done": true}` if Home Assistant made the bottle glow itself, or `{"done": false, "find": {"characteristic": "<GLOW_NOW uuid>", "hex": "b4"}}` |
| `begin` | `{"step": "begin", "firmware": "90.11"}` | `{"go": true, "ask": "57", "acknowledge": null, "idle_seconds": 10}`, or `{"go": false, "reason": "unsupported"}` |
| `sips` | `{"step": "sips", "firmware": "90.11", "connected": true, "battery": 80, "commands": "80000000", "records": [{"hex": "…", "received": 1800000000.5}]}` | `{"received": 1, "saved": 1, "writes": [{"characteristic": "<uuid>", "hex": "…"}]}` |

`received` in a record is the phone's clock, in seconds since 1970, when the notification arrived.
`ask` is written to SIPS to request the sips, and written again whenever the bottle answers with a
count of sips still waiting. `acknowledge`, when it isn't null, is written to SIPS after each record
is stored. Reading stops when nothing has arrived for `idle_seconds`.

`find` is the one write that makes the bottle glow, in the style set by the **Glow** option. Every
check carries it, and the app keeps the latest, so Find still works when Home Assistant can't be
reached.

`connected` is false when the records are from an earlier run. They're saved, and nothing else
changes: no battery reading, no last sync time, no writes.

A request that can't be used gets status 400 with `{"error": "…"}` and saves nothing. When one
record is at fault and could never be used, such as one that isn't hex or is dated before the 90
days of sips Home Assistant keeps, the reply also has `"record"`, its place in the list counting
from 0. The app moves that one record to a `refused.jsonl` file in its storage, so it's neither lost
nor retried for ever, sends the rest again, and says in its log how many it set aside. It stops
after 20 in one run.

Any other refusal leaves every record on the phone for the next run, and the app's log gives the
reason. That covers a record dated more than five minutes ahead, which means the phone's clock is
wrong and the record may be fine once it's right, and an answer from something that isn't the relay.
While the integration is reloading, the reply is status 503, and the phone keeps its records the same way.

## iPhone: not built, and a welcome contribution

There's no relay for iPhone. The Home Assistant app can't stand in for one on either platform,
because it doesn't open Bluetooth connections to devices, and neither can a browser or a Shortcut on
iOS. It has to be an app of its own, and nobody here has an iPhone to build and test one with. The
iOS Simulator has no Bluetooth, so it can only be proven on a real phone beside a real bottle.

Nothing on the Home Assistant side is tied to Android. An iPhone app that speaks
[the messages](#the-messages) above and follows [the steps of a run](#what-happens-on-each-run)
would work with the integration as it is. If you write one, these are the parts that matter:

- Store each record before doing anything else with it, and drop it only when Home Assistant
  confirms it. The bottle hands each sip over once.
- Write only what the replies ask for, and only to COMMANDS or SCHEDULE. The characteristics are in
  [protocol.md](protocol.md).
- Don't read WEIGHT.
- The Android app wakes every 30 minutes and scans. iOS won't run an app on a timer like that; the
  usual way there is to leave a connection request pending for the known bottle and let the system
  wake the app when it comes into range. How well that works for these bottles is the open question.

Open an issue before you start, so the work isn't done twice. It will be listed as Untested until
someone has seen it sync a real bottle.
