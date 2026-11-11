package io.github.tech2urdoor.bottlerelay

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.UUID

private const val GLOW = "a1d9a5bf-f5d8-49f3-a440-e6bf27440cb0"

private fun find(hex: String = "34", characteristic: String = GLOW): JSONObject =
    JSONObject().put("characteristic", characteristic).put("hex", hex)

private class GlowBottle(private val fails: Boolean = false) : Link {
    val writes = mutableListOf<Pair<UUID, String>>()
    var closed = false

    override fun read(characteristic: UUID): ByteArray = throw IOException("not read here")

    override fun write(characteristic: UUID, value: ByteArray) {
        if (fails) throw IOException("dropped")
        writes += characteristic to Relay.hex(value)
    }

    override fun subscribe(characteristic: UUID) = Unit

    override fun next(seconds: Double): Notification? = null

    override fun close() {
        closed = true
    }
}

private class FindHome(private val answer: () -> Reply) : Home {
    var calls = 0

    override fun post(body: JSONObject): Reply {
        calls += 1
        assertEquals("find", body.getString("step"))
        return answer()
    }
}

class FinderTest {
    private var saved: JSONObject? = null

    private fun finder(home: Home?, bottle: Link?) =
        Finder(home, saved = { saved }, remember = { saved = it }, connect = { bottle })

    @Test
    fun `away from home it writes what Home Assistant sends, and keeps it`() {
        val bottle = GlowBottle()
        val home = FindHome { Reply(200, JSONObject().put("done", false).put("find", find("b4"))) }
        assertEquals("The bottle should be glowing", finder(home, bottle).run())
        assertEquals(listOf(Finder.GLOW_NOW to "b4"), bottle.writes)
        assertTrue(bottle.closed)
        assertEquals("b4", saved?.getString("hex"))
    }

    @Test
    fun `at home Home Assistant does it and the phone doesn't connect`() {
        val home = FindHome { Reply(200, JSONObject().put("done", true)) }
        var connected = false
        val result = Finder(home, { null }, {}, connect = { connected = true; GlowBottle() }).run()
        assertEquals("Home Assistant made the bottle glow", result)
        assertEquals(false, connected)
    }

    @Test
    fun `with Home Assistant out of reach the saved instruction is used`() {
        saved = find("2c")
        val bottle = GlowBottle()
        val home = FindHome { throw IOException("offline") }
        assertEquals("The bottle should be glowing", finder(home, bottle).run())
        assertEquals(listOf(Finder.GLOW_NOW to "2c"), bottle.writes)
    }

    @Test
    fun `an odd answer from Home Assistant falls back to the saved instruction`() {
        saved = find("2c")
        val bottle = GlowBottle()
        finder(FindHome { Reply(404, null) }, bottle).run()
        assertEquals(listOf(Finder.GLOW_NOW to "2c"), bottle.writes)
    }

    @Test
    fun `without an address the saved instruction is used`() {
        saved = find("30")
        val bottle = GlowBottle()
        finder(null, bottle).run()
        assertEquals(listOf(Finder.GLOW_NOW to "30"), bottle.writes)
    }

    @Test
    fun `nothing to go on means nothing is written`() {
        val bottle = GlowBottle()
        val result = finder(FindHome { throw IOException("offline") }, bottle).run()
        assertTrue(result.startsWith("Sync once"))
        assertTrue(bottle.writes.isEmpty())
    }

    @Test
    fun `only one byte to GLOW_NOW is ever written`() {
        val sips = "016e11b1-6c8a-4074-9e5a-076053f93784"
        for (bad in listOf(find(characteristic = sips), find(hex = "3434"), find(hex = ""), find(hex = "zz"), find(characteristic = "nope"))) {
            val bottle = GlowBottle()
            val result = finder(FindHome { Reply(200, JSONObject().put("done", false).put("find", bad)) }, bottle).run()
            assertTrue(bad.toString(), result.contains("isn't Find"))
            assertTrue(bottle.writes.isEmpty())
            assertNull(Finder.allowed(bad))
        }
    }

    @Test
    fun `a bottle out of range or dropping says so`() {
        saved = find()
        assertEquals("The bottle isn't nearby", finder(null, null).run())
        val dropping = GlowBottle(fails = true)
        assertTrue(finder(null, dropping).run().startsWith("Connected, but"))
        assertTrue(dropping.closed)
    }

    @Test
    fun `a refused instruction isn't kept`() {
        saved = find("b4")
        finder(FindHome { Reply(200, JSONObject().put("done", false).put("find", find(hex = "3434"))) }, GlowBottle()).run()
        assertEquals("b4", saved?.getString("hex"))
    }

    @Test
    fun `each sync keeps the instruction the check carries, at home or away`() {
        for (go in listOf(true, false)) {
            var kept: JSONObject? = null
            val home = Home { Reply(200, JSONObject().put("go", go).put("find", find("b0"))) }
            Relay(home, Outbox(createTempDir()), connect = { null }, rememberFind = { kept = it }).run()
            assertEquals("b0", kept?.getString("hex"))
        }
    }

    @Test
    fun `signal in words`() {
        assertEquals("Very close", Finder.closeness(-50))
        assertEquals("Close", Finder.closeness(-70))
        assertEquals("In range, further off", Finder.closeness(-80))
        assertEquals("Faint", Finder.closeness(-95))
    }
}
