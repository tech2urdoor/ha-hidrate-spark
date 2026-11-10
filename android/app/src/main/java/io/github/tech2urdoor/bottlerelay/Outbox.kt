package io.github.tech2urdoor.bottlerelay

import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream

data class Record(val hex: String, val received: Double, val firmware: String)

/**
 * Records read off the bottle that Home Assistant hasn't confirmed yet, one to a line.
 * Each is forced to disk as it's added, because the bottle won't hand it over twice.
 */
class Outbox(private val directory: File) {
    private val waiting = File(directory, "outbox.jsonl")
    private val refused = File(directory, "refused.jsonl")

    @Synchronized
    fun add(record: Record) = append(waiting, listOf(record))

    @Synchronized
    fun all(): List<Record> = read(waiting)

    @Synchronized
    fun remove(records: List<Record>) {
        val left = read(waiting) - records.toSet()
        val fresh = File(directory, "outbox.jsonl.new")
        fresh.delete()
        append(fresh, left)
        if (!fresh.renameTo(waiting)) {
            waiting.delete()
            fresh.renameTo(waiting)
        }
    }

    /** Home Assistant said these can't be used. Kept out of the way rather than deleted or sent for ever. */
    @Synchronized
    fun setAside(records: List<Record>) {
        append(refused, records)
        remove(records)
    }

    private fun append(file: File, records: List<Record>) {
        FileOutputStream(file, true).use { stream ->
            for (record in records) {
                val line = JSONObject()
                    .put("hex", record.hex)
                    .put("received", record.received)
                    .put("firmware", record.firmware)
                stream.write((line.toString() + "\n").toByteArray())
            }
            stream.fd.sync()
        }
    }

    private fun read(file: File): List<Record> {
        if (!file.exists()) return emptyList()
        return file.readLines().mapNotNull { line ->
            try {
                val row = JSONObject(line)
                Record(row.getString("hex"), row.getDouble("received"), row.getString("firmware"))
            } catch (err: JSONException) {
                null
            }
        }
    }
}
