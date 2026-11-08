# Contributing

The most useful contribution is a report on your own bottle, and it needs no coding at all.

## Report your bottle

1. Install the integration ([README](README.md#install)) and close the Hidrate app.
2. In Home Assistant, go to **Developer Tools → Actions** and run `hidrate_spark.probe`.
3. [Open a bottle report](https://github.com/tech2urdoor/ha-hidrate-spark/issues/new?template=bottle-report.yml)
   and paste the result.

The probe is read-only. It takes no sips and writes nothing, so running it can't cost you any data.

**It's safe to paste, but read it first.** Your serial number and Bluetooth addresses are removed,
and the bottle's name is cut down to `h2o****`. Two things to look over:

- It includes raw values from the bottle's settings, which may reflect how much you drank today and
  your reminder times. Delete those lines if you'd rather not share them.
- Logs aren't covered. If you paste a log as well, take out anything like `AA:BB:CC:DD:EE:FF`, and
  your bottle's serial number.

**If Home Assistant can't find your bottle,** the probe has nothing to connect to. Use a Bluetooth
scanner app such as nRF Connect, and report the start of its name and the service UUIDs it
advertises. Leave out the address.

## How a bottle gets supported

`Firmware.supported` in `custom_components/hidrate_spark/protocol.py` decides which bottles the
integration will read. It starts narrow because sips are one-shot: a wrong guess could lose them.
It grows only with evidence, in these steps:

1. **Probe.** A report shows the firmware version and which characteristics exist. If they match a
   bottle already known to work, that's a good sign but not proof.
2. **Read.** The owner tries it, knowingly, and compares sip amounts and times with what they drank.
   To try a version outside the range, edit `supported` in your own copy. Expect to lose whatever
   sips were on the bottle if it goes wrong.
3. **Reminders.** With reminders on, the owner watches for the bottle to glow when it should, with
   nothing else connected. Reading the schedule back doesn't prove it: the one bottle tested returns
   zeros.
4. **Merge.** A pull request widens the range by the smallest amount the evidence supports, adds the
   version to the tests, and fills in a row of the compatibility table in the README.

Each cell in that table says **Works** only if someone has seen it work on a real bottle. Please
keep it that way.

Firmware 8–19 and 30–39 are attempted without having been run, because the protocol for them is
well understood, and every record from them is saved whole so a wrong reading can be redone.
Firmware below 8, 20–29 and Telink bottles (100 and up) need more than a wider range. See
[the protocol notes](docs/protocol.md#reading-sips).

## Changing the code

The tests need only Python, with no Home Assistant and nothing to install:

```bash
python3 tests/test_hidrate_spark.py
```

Everything that touches bytes lives in `protocol.py`, `session.py` and `sip_log.py`, which don't
import Home Assistant. Keep it that way, so each byte can be pinned by a test.

CI also imports every module and tests the coordinator against a real Home Assistant, which is what
notices a name Home Assistant doesn't have. To run those yourself, in a virtual environment:

```bash
pip install homeassistant
python tests/check_imports.py --requirements | pip install -r /dev/stdin
python tests/check_imports.py
python tests/test_coordinator.py
```

Some rules hold for every change:

- **Never write to anything in `NEVER_WRITE`,** and never start a firmware update. A test holds this.
- **A new write needs its own justification.** Add a test for its bytes, and describe it in
  [docs/what-it-writes.md](docs/what-it-writes.md) in plain language.
- **Never decode on a guess.** An unknown firmware gets refused before its first sip. A firmware
  that's attempted without having been run keeps its raw records, so nothing it hands over is lost.
- **Nothing from Hidrate goes in this repository.** No code, text, images or files from their app,
  including decompiled code, even in an issue. Describe what the bottle does, in your own words. The
  bytes themselves are fine, because they're facts about how the bottle communicates.
- **Keep real identities out.** Use made-up values for addresses, serials and sips in tests and docs.
  `AA:BB:CC:DD:EE:FF` is the placeholder address, and a test rejects others.
- **Update the protocol notes** when you learn something, and say how you know it.

CI runs hassfest, HACS validation and the tests on every pull request.

## Licence

Contributions are accepted under the [MIT licence](LICENSE).
