# Bottle Relay

A small Android app that reads a bottle while it's away from home and hands the sips to the
integration in Home Assistant. What it does and how to set it up:
[docs/phone-relay.md](../docs/phone-relay.md).

**Untested on a real bottle.**

## Building

You need a JDK (17 or later) and the Android SDK with platform 36, both of which come with Android
Studio. From this folder:

```bash
./gradlew testDebugUnitTest assembleDebug
```

The app is then at `app/build/outputs/apk/debug/app-debug.apk`. With the phone plugged in and USB
debugging on:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## What's in it

| File | What it does |
|---|---|
| `Relay.kt` | One sync, start to finish. No Android in it, so the tests run it against a fake bottle |
| `Outbox.kt` | Records read off the bottle, kept on disk until Home Assistant confirms them |
| `GattLink.kt` | The Bluetooth connection and the scan |
| `Syncer.kt` | Settings, the call to Home Assistant, and the 30-minute schedule |
| `MainActivity.kt` | The one screen |

It asks for **Nearby devices** and the internet, and nothing else. It keeps the relay address, the
chosen bottle's address and name, and the last 40 results, all in the app's private storage.
