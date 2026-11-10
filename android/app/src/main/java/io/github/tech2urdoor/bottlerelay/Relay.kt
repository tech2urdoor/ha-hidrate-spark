package io.github.tech2urdoor.bottlerelay

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

/** One notification from the bottle, with the phone's clock when it arrived, in seconds. */
class Notification(val value: ByteArray, val received: Double)

/** One connection to the bottle. Every call fails with an IOException if the bottle has gone. */
interface Link : AutoCloseable {
    fun read(characteristic: UUID): ByteArray
    fun write(characteristic: UUID, value: ByteArray)
    fun subscribe(characteristic: UUID)

    /** Null when nothing arrived in that time. */
    fun next(seconds: Double): Notification?
}

class Reply(val code: Int, val body: JSONObject?)

/** Home Assistant's relay address. Fails with an IOException when it can't be reached. */
fun interface Home {
    fun post(body: JSONObject): Reply
}

/**
 * Carries bytes between the bottle and Home Assistant, which decides everything: whether to read,
 * what to ask the bottle for, what the records mean and what to write back.
 *
 * A sip handed over by the bottle is gone from it, so each one is on disk before the next is
 * asked for, and stays there until Home Assistant says it has it.
 */
class Relay(
    private val home: Home,
    private val outbox: Outbox,
    private val connect: () -> Link?,
    private val now: () -> Double = { System.currentTimeMillis() / 1000.0 },
) {
    fun run(): String {
        val late = try {
            deliverLate()
        } catch (err: IOException) {
            return "Home Assistant can't be reached, so the bottle was left alone"
        }
        val check = try {
            home.post(JSONObject().put("step", "check"))
        } catch (err: IOException) {
            return "Home Assistant can't be reached, so the bottle was left alone"
        }
        val go = check.body?.takeIf { check.code == 200 && it.has("go") }
            ?: return "That address isn't a relay that's turned on (${check.code})"
        if (!go.optBoolean("go")) return "The bottle is at home, so Home Assistant reads it$late"

        val link = try {
            connect()
        } catch (err: IOException) {
            return "Couldn't connect to the bottle: ${err.message}$late"
        } ?: return "The bottle isn't nearby$late"
        return link.use { session(it) } + late
    }

    private fun session(link: Link): String {
        val firmware: String
        val begin: JSONObject
        try {
            firmware = text(link.read(FIRMWARE))
            val reply = home.post(JSONObject().put("step", "begin").put("firmware", firmware))
            begin = reply.body?.takeIf { reply.code == 200 }
                ?: return "Home Assistant didn't say how to read firmware $firmware (${reply.code})"
        } catch (err: IOException) {
            return "Stopped before reading anything: ${err.message}"
        }
        if (!begin.optBoolean("go")) return "Home Assistant can't read a bottle with firmware $firmware"

        var connected = true
        var battery: Int? = null
        var status: ByteArray? = null
        try {
            takeSips(link, firmware, begin)
            battery = optional { link.read(BATTERY).firstOrNull()?.toInt() }
            status = optional { link.read(COMMANDS) }
        } catch (err: IOException) {
            connected = false
        }

        val sent = outbox.all()
        val body = sipsBody(sent, firmware, connected)
        battery?.let { body.put("battery", it) }
        status?.let { body.put("commands", hex(it)) }
        val reply = try {
            home.post(body)
        } catch (err: IOException) {
            return "Read ${sent.size} records and kept them until Home Assistant can be reached"
        }
        val saved = settle(reply, sent)
            ?: return "Home Assistant didn't take ${sent.size} records (${reply.code}), so they're kept"
        if (!connected) return "Lost the bottle part-way. $saved new sips reached Home Assistant"

        val writes = reply.body?.optJSONArray("writes") ?: JSONArray()
        try {
            for (index in 0 until writes.length()) {
                val write = writes.getJSONObject(index)
                val characteristic = UUID.fromString(write.getString("characteristic"))
                // Whatever the reply says, nothing is written anywhere but these two.
                if (characteristic != COMMANDS && characteristic != SCHEDULE) {
                    return "Synced, $saved new sips. Stopped at a write to somewhere unexpected"
                }
                link.write(characteristic, unhex(write.getString("hex")))
            }
        } catch (err: Exception) {
            return "Synced, $saved new sips. The reminders weren't refreshed: ${err.message}"
        }
        return "Synced, $saved new sips"
    }

    private fun takeSips(link: Link, firmware: String, begin: JSONObject) {
        val ask = unhex(begin.getString("ask"))
        val acknowledge = if (begin.isNull("acknowledge")) null else unhex(begin.getString("acknowledge"))
        val idle = begin.optDouble("idle_seconds", 10.0).coerceIn(1.0, 30.0)
        val deadline = now() + READ_LIMIT_SECONDS
        link.subscribe(SIPS)
        link.write(SIPS, ask)
        while (true) {
            val wait = minOf(idle, deadline - now())
            if (wait <= 0) break
            val notification = link.next(wait) ?: break
            val data = notification.value
            if (holdsNothing(data)) {
                if (data.isEmpty() || data[0].toInt() == 0) break
                link.write(SIPS, ask)
                continue
            }
            outbox.add(Record(hex(data), notification.received, firmware))
            // Only once the record is on disk, so a dropped link can't lose one the bottle let go of.
            if (acknowledge != null) link.write(SIPS, acknowledge)
        }
    }

    /** Records read on an earlier run that never reached Home Assistant. */
    private fun deliverLate(): String {
        val waiting = outbox.all()
        if (waiting.isEmpty()) return ""
        val reply = home.post(sipsBody(waiting, waiting.first().firmware, connected = false))
        val saved = settle(reply, waiting) ?: return ""
        return ". $saved sips from earlier were handed over"
    }

    /** Null if Home Assistant didn't answer as the relay does, and then nothing is removed. */
    private fun settle(reply: Reply, sent: List<Record>): Int? {
        if (reply.code == 400) {
            outbox.setAside(sent)
            return 0
        }
        val body = reply.body?.takeIf { reply.code == 200 && it.has("received") } ?: return null
        outbox.remove(sent)
        return body.optInt("saved")
    }

    private fun sipsBody(records: List<Record>, firmware: String, connected: Boolean): JSONObject {
        val rows = JSONArray()
        for (record in records) rows.put(JSONObject().put("hex", record.hex).put("received", record.received))
        return JSONObject()
            .put("step", "sips")
            .put("firmware", firmware)
            .put("connected", connected)
            .put("records", rows)
    }

    private fun <T> optional(read: () -> T): T? = try {
        read()
    } catch (err: IOException) {
        null
    }

    companion object {
        const val READ_LIMIT_SECONDS = 180.0

        val FIRMWARE: UUID = UUID.fromString("00002a26-0000-1000-8000-00805f9b34fb")
        val BATTERY: UUID = UUID.fromString("00002a19-0000-1000-8000-00805f9b34fb")
        val SIPS: UUID = UUID.fromString("016e11b1-6c8a-4074-9e5a-076053f93784")
        val COMMANDS: UUID = UUID.fromString("e3578b0d-caa7-46d6-b7c2-7331c08de044")
        val SCHEDULE: UUID = UUID.fromString("b44b03f0-b850-4090-86eb-72863fb3618d")
        val SERVICE: UUID = UUID.fromString("45855422-6565-4cd7-a2a9-fe8af41b85e8")

        /**
         * A short packet only counts the sips still waiting, and an all-zero record is the bottle
         * saying it has nothing. Home Assistant checks every record again, so this only decides
         * whether to ask for the next one.
         */
        fun holdsNothing(data: ByteArray): Boolean {
            if (data.size < 16) return true
            val idle = (2..7).all { data[it].toInt() == 0 }
            return idle && data[12] == data[14] && data[13] == data[15]
        }

        fun text(data: ByteArray): String =
            String(data.takeWhile { it.toInt() != 0 }.toByteArray(), Charsets.US_ASCII).trim()

        fun hex(data: ByteArray): String = data.joinToString("") { "%02x".format(it) }

        fun unhex(text: String): ByteArray {
            require(text.length % 2 == 0) { "odd-length hex" }
            return ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        }
    }
}
