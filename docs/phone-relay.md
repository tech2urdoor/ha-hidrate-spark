# Phone relay: syncing while the bottle is away from home

Home Assistant can only read a bottle that's in range of one of its Bluetooth proxies. The relay is a
small Android app for a phone that stays near the bottle when it's out: it connects to the bottle,
passes what it reads to Home Assistant over the internet, and makes the reminder writes Home Assistant
asks for. It's optional and off by default.

**Untested on a real bottle.** The integration's side and the app's logic are covered by tests with a
fake bottle. The app has been built, and nobody has yet run it against a real one.

The phone decides nothing. It doesn't know what a record means, how big your bottle is or what your
goal is. Home Assistant tells it which byte asks for the sips, works out the sips from the raw
records, and says which bytes to write afterwards, exactly as it would on its own connection.

## Setting it up

1. Your Home Assistant has to be reachable from where the phone will be, at an `https` address.
2. In Home Assistant, open the bottle's **Configure** and tick **Let a phone read the bottle away from
   home**. The next screen shows the relay address. Anyone who has it can add sips to that bottle, so
   treat it like a password.
3. Build the app from [`android/`](../android) and install it. It needs Android 13 or later.
4. In the app, paste the relay address and save it, choose your bottle, and turn on **Sync every 30
   minutes**. Allow **Nearby devices** when asked. It never asks for your location.

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
5. It reads the battery level and the status bytes, and sends everything to Home Assistant.
6. Home Assistant saves the sips, with the times they were drunk, and replies with the reminder writes
   if you have reminders on. The phone makes them and disconnects.

A record leaves the phone's storage only when Home Assistant confirms it. If the internet drops
part-way, the records go on the next run. Sending the same record twice counts once.

## What the phone writes to the bottle

The same things a sync from Home Assistant writes, listed in [what-it-writes.md](what-it-writes.md):
the request for sips, the `0x33` after each sip on firmware below 30, and the reminder writes.
The app refuses any write that isn't to COMMANDS or SCHEDULE, whatever a reply says, and the
integration refuses to ask for one. The app can't change the glow colour, calibrate or run Find.

It never reads WEIGHT, so `Water left` comes from sips while the bottle is away.

## The messages

Every call is a JSON `POST` to the relay address. Byte strings are lower-case hex.

| Step | The phone sends | Home Assistant replies |
|---|---|---|
| `check` | `{"step": "check"}` | `{"go": true}`, or `{"go": false, "reason": "in_range"}` |
| `begin` | `{"step": "begin", "firmware": "90.11"}` | `{"go": true, "ask": "57", "acknowledge": null, "idle_seconds": 10}`, or `{"go": false, "reason": "unsupported"}` |
| `sips` | `{"step": "sips", "firmware": "90.11", "connected": true, "battery": 80, "commands": "80000000", "records": [{"hex": "…", "received": 1800000000.5}]}` | `{"received": 1, "saved": 1, "writes": [{"characteristic": "<uuid>", "hex": "…"}]}` |

`received` in a record is the phone's clock, in seconds since 1970, when the notification arrived.
`ask` is written to SIPS to request the sips, and written again whenever the bottle answers with a
count of sips still waiting. `acknowledge`, when it isn't null, is written to SIPS after each record
is stored. Reading stops when nothing has arrived for `idle_seconds`.

`connected` is false when the records are from an earlier run. They're saved, and nothing else
changes: no battery reading, no last sync time, no writes.

A request that can't be used gets status 400 with `{"error": "…"}` and saves nothing. The app then
moves those records to a `refused.jsonl` file in its storage, so they're neither lost nor retried for ever.
