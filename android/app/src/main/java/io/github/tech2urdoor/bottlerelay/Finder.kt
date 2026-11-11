package io.github.tech2urdoor.bottlerelay

import org.json.JSONObject
import java.io.IOException
import java.util.UUID

/**
 * Makes the bottle glow so it can be found. Home Assistant says what to write, in the glow style
 * picked there, and at home it makes the bottle glow itself. Away from home, or with Home Assistant
 * out of reach, the phone writes the last instruction it was given.
 *
 * The only write allowed here is one byte to GLOW_NOW, whatever a reply says.
 */
class Finder(
    private val home: Home?,
    private val saved: () -> JSONObject?,
    private val remember: (JSONObject) -> Unit,
    private val connect: () -> Link?,
) {
    fun run(): String {
        var find: JSONObject? = null
        if (home != null) {
            try {
                val reply = home.post(JSONObject().put("step", "find"))
                val body = reply.body?.takeIf { reply.code == 200 }
                if (body?.optBoolean("done") == true) return "Home Assistant made the bottle glow"
                find = body?.optJSONObject("find")?.also { if (allowed(it) != null) remember(it) }
            } catch (err: IOException) {
                // Home Assistant out of reach: the saved instruction is just as good.
            }
        }
        find = find ?: saved() ?: return "Sync once with Home Assistant first, so the phone knows how this bottle glows"
        val value = allowed(find) ?: return "Home Assistant asked for a write that isn't Find, so nothing was written"

        val link = try {
            connect()
        } catch (err: IOException) {
            return "Couldn't connect to the bottle: ${err.message}"
        } ?: return "The bottle isn't nearby"
        return link.use {
            try {
                it.write(GLOW_NOW, value)
                "The bottle should be glowing"
            } catch (err: IOException) {
                "Connected, but the glow didn't go through: ${err.message}"
            }
        }
    }

    companion object {
        val GLOW_NOW: UUID = UUID.fromString("a1d9a5bf-f5d8-49f3-a440-e6bf27440cb0")

        /** The byte to write, or null if the instruction is anything but one byte to GLOW_NOW. */
        fun allowed(find: JSONObject): ByteArray? {
            val characteristic = try {
                UUID.fromString(find.optString("characteristic"))
            } catch (err: IllegalArgumentException) {
                return null
            }
            if (characteristic != GLOW_NOW) return null
            val value = try {
                Relay.unhex(find.optString("hex"))
            } catch (err: IllegalArgumentException) {
                return null
            }
            return value.takeIf { it.size == 1 }
        }

        /** Signal strength in plain words, for walking towards the bottle. */
        fun closeness(rssi: Int): String = when {
            rssi >= -60 -> "Very close"
            rssi >= -72 -> "Close"
            rssi >= -85 -> "In range, further off"
            else -> "Faint"
        }
    }
}
