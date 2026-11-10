package io.github.tech2urdoor.bottlerelay

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException
import java.util.UUID

private fun record(remaining: Int = 0, start: Int = 400, stop: Int = 300): ByteArray {
    val data = ByteArray(20)
    data[0] = remaining.toByte()
    data[2] = 1
    data[4] = 30
    data[12] = (start and 0xFF).toByte()
    data[13] = (start shr 8).toByte()
    data[14] = (stop and 0xFF).toByte()
    data[15] = (stop shr 8).toByte()
    return data
}

/** A bottle that answers each request for sips with the next packet it was given. */
private class FakeBottle(private val packets: MutableList<ByteArray>, private val dropAfter: Int = Int.MAX_VALUE) : Link {
    val writes = mutableListOf<Pair<UUID, String>>()
    var closed = false
    private var sent = 0
    private var asked = false

    override fun read(characteristic: UUID): ByteArray = when (characteristic) {
        Relay.FIRMWARE -> "90.11".toByteArray() + byteArrayOf(0)
        Relay.BATTERY -> byteArrayOf(80)
        Relay.COMMANDS -> byteArrayOf(0x80.toByte(), 0x20, 0, 0)
        else -> throw IOException("no such characteristic")
    }

    override fun write(characteristic: UUID, value: ByteArray) {
        writes += characteristic to Relay.hex(value)
        if (characteristic == Relay.SIPS && Relay.hex(value) == "57") asked = true
    }

    override fun subscribe(characteristic: UUID) = Unit

    override fun next(seconds: Double): Notification? {
        if (sent >= dropAfter) throw IOException("the bottle disconnected")
        // A full record is followed by the next without asking. After anything else the bottle waits to be asked.
        if (!asked || packets.isEmpty()) return null
        val packet = packets.removeAt(0)
        asked = !Relay.holdsNothing(packet)
        sent += 1
        return Notification(packet, 1000.0 + sent)
    }

    override fun close() {
        closed = true
    }
}

private class FakeHome(private val writes: JSONArray = JSONArray()) : Home {
    val bodies = mutableListOf<JSONObject>()
    var go = true
    var reachable = true
    var sipsCode = 200
    var firmwareGo = true

    override fun post(body: JSONObject): Reply {
        if (!reachable) throw IOException("unreachable")
        bodies += JSONObject(body.toString())
        return when (body.getString("step")) {
            "check" -> Reply(200, JSONObject().put("go", go))
            "begin" -> Reply(
                200,
                JSONObject().put("go", firmwareGo).put("ask", "57").put("acknowledge", JSONObject.NULL).put("idle_seconds", 10),
            )
            else -> if (sipsCode == 200) {
                val count = body.getJSONArray("records").length()
                Reply(200, JSONObject().put("received", count).put("saved", count).put("writes", writes))
            } else {
                Reply(sipsCode, null)
            }
        }
    }

    fun sips(): List<JSONObject> = bodies.filter { it.getString("step") == "sips" }
}

class RelayTest {
    @get:Rule
    val folder = TemporaryFolder()
    private lateinit var outbox: Outbox

    @Before
    fun setUp() {
        outbox = Outbox(folder.root)
    }

    private fun relay(home: Home, bottle: Link?) = Relay(home, outbox, connect = { bottle }, now = { 0.0 })

    @Test
    fun sipsReachHomeAssistantAndTheOutboxEmpties() {
        val home = FakeHome()
        val bottle = FakeBottle(mutableListOf(record(remaining = 1), record(), ByteArray(1)))
        val result = relay(home, bottle).run()
        assertEquals("Synced, 2 new sips", result)
        val sent = home.sips().single()
        assertEquals(2, sent.getJSONArray("records").length())
        assertEquals(Relay.hex(record(remaining = 1)), sent.getJSONArray("records").getJSONObject(0).getString("hex"))
        assertEquals(1001.0, sent.getJSONArray("records").getJSONObject(0).getDouble("received"), 0.0)
        assertTrue(sent.getBoolean("connected"))
        assertEquals("90.11", sent.getString("firmware"))
        assertEquals(80, sent.getInt("battery"))
        assertEquals("80200000", sent.getString("commands"))
        assertTrue(outbox.all().isEmpty())
        assertTrue(bottle.closed)
    }

    @Test
    fun aShortPacketWithMoreWaitingIsAskedAgain() {
        val home = FakeHome()
        val bottle = FakeBottle(mutableListOf(byteArrayOf(2), record(), ByteArray(16)))
        relay(home, bottle).run()
        assertEquals(1, home.sips().single().getJSONArray("records").length())
        assertEquals(2, bottle.writes.count { it == Relay.SIPS to "57" })
    }

    @Test
    fun atHomeTheBottleIsLeftAlone() {
        val home = FakeHome().apply { go = false }
        val bottle = FakeBottle(mutableListOf(record()))
        assertEquals("The bottle is at home, so Home Assistant reads it", relay(home, bottle).run())
        assertTrue(bottle.writes.isEmpty())
    }

    @Test
    fun withHomeAssistantOutOfReachNothingIsTakenOffTheBottle() {
        val home = FakeHome().apply { reachable = false }
        val bottle = FakeBottle(mutableListOf(record()))
        relay(home, bottle).run()
        assertTrue(bottle.writes.isEmpty())
        assertTrue(outbox.all().isEmpty())
    }

    @Test
    fun anAddressThatIsntTheRelayIsNotTrusted() {
        val wrong = Home { Reply(200, null) }
        val bottle = FakeBottle(mutableListOf(record()))
        assertTrue(relay(wrong, bottle).run().startsWith("That address isn't a relay"))
        assertTrue(bottle.writes.isEmpty())
    }

    @Test
    fun unsupportedFirmwareTakesNoSips() {
        val home = FakeHome().apply { firmwareGo = false }
        val bottle = FakeBottle(mutableListOf(record()))
        relay(home, bottle).run()
        assertTrue(bottle.writes.isEmpty())
    }

    @Test
    fun sipsReadBeforeHomeAssistantDroppedOutAreKeptAndSentNextTime() {
        val bottle = FakeBottle(mutableListOf(record(), ByteArray(1)))
        var calls = 0
        val flaky = Home { body ->
            calls += 1
            if (body.getString("step") == "sips") throw IOException("gone")
            FakeHome().post(body)
        }
        assertTrue(relay(flaky, bottle).run().startsWith("Read 1 records and kept them"))
        assertEquals(1, outbox.all().size)

        val home = FakeHome().apply { go = false }
        val result = relay(home, null).run()
        assertEquals("The bottle is at home, so Home Assistant reads it. 1 sips from earlier were handed over", result)
        assertFalse(home.sips().single().getBoolean("connected"))
        assertTrue(outbox.all().isEmpty())
    }

    @Test
    fun aBottleLostPartWayStillHandsOverWhatItGave() {
        val home = FakeHome()
        val bottle = FakeBottle(mutableListOf(record(remaining = 1), record()), dropAfter = 1)
        val result = relay(home, bottle).run()
        assertEquals("Lost the bottle part-way. 1 new sips reached Home Assistant", result)
        assertFalse(home.sips().single().getBoolean("connected"))
        assertTrue(outbox.all().isEmpty())
    }

    @Test
    fun aServerErrorKeepsTheRecords() {
        val home = FakeHome().apply { sipsCode = 500 }
        relay(home, FakeBottle(mutableListOf(record(), ByteArray(1)))).run()
        assertEquals(1, outbox.all().size)
    }

    @Test
    fun recordsHomeAssistantRefusesAreSetAsideNotSentForEver() {
        val home = FakeHome().apply { sipsCode = 400 }
        relay(home, FakeBottle(mutableListOf(record(), ByteArray(1)))).run()
        assertTrue(outbox.all().isEmpty())
        assertTrue(folder.root.resolve("refused.jsonl").readText().contains(Relay.hex(record())))
    }

    @Test
    fun theWritesHomeAssistantAsksForAreMadeInOrder() {
        val writes = JSONArray()
            .put(JSONObject().put("characteristic", Relay.COMMANDS.toString()).put("hex", "210010"))
            .put(JSONObject().put("characteristic", Relay.SCHEDULE.toString()).put("hex", "933d"))
        val bottle = FakeBottle(mutableListOf(ByteArray(1)))
        assertEquals("Synced, 0 new sips", relay(FakeHome(writes), bottle).run())
        assertEquals(
            listOf(Relay.SIPS to "57", Relay.COMMANDS to "210010", Relay.SCHEDULE to "933d"),
            bottle.writes,
        )
    }

    @Test
    fun aWriteToAnywhereElseIsNeverMade() {
        val elsewhere = "316c4914-1f59-462e-af06-185418674c0c"
        val writes = JSONArray().put(JSONObject().put("characteristic", elsewhere).put("hex", "00"))
        val bottle = FakeBottle(mutableListOf(ByteArray(1)))
        relay(FakeHome(writes), bottle).run()
        assertEquals(listOf(Relay.SIPS to "57"), bottle.writes)
    }

    @Test
    fun aBottleOutOfRangeIsSaidPlainly() {
        assertEquals("The bottle isn't nearby", relay(FakeHome(), null).run())
    }

    @Test
    fun whatCountsAsNothing() {
        assertTrue(Relay.holdsNothing(ByteArray(0)))
        assertTrue(Relay.holdsNothing(byteArrayOf(3)))
        assertTrue(Relay.holdsNothing(ByteArray(16)))
        assertFalse(Relay.holdsNothing(record()))
        assertEquals("90.11", Relay.text("90.11".toByteArray() + ByteArray(3)))
        assertEquals("0a1bff", Relay.hex(Relay.unhex("0a1bff")))
    }

    @Test
    fun theOutboxSurvivesBeingReopened() {
        outbox.add(Record("aa", 1.5, "90.11"))
        outbox.add(Record("bb", 2.5, "90.11"))
        val again = Outbox(folder.root)
        assertEquals(listOf(Record("aa", 1.5, "90.11"), Record("bb", 2.5, "90.11")), again.all())
        again.remove(listOf(Record("aa", 1.5, "90.11")))
        assertEquals(listOf(Record("bb", 2.5, "90.11")), Outbox(folder.root).all())
    }
}
