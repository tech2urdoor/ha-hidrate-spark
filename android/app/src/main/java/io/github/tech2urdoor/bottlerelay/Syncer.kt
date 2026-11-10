package io.github.tech2urdoor.bottlerelay

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONException
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock

/** What the app remembers: the relay address, the chosen bottle and the last few results. */
class Settings(context: Context) {
    private val store = context.getSharedPreferences("relay", Context.MODE_PRIVATE)

    var url: String
        get() = store.getString("url", "") ?: ""
        set(value) = store.edit().putString("url", value).apply()

    var bottleAddress: String
        get() = store.getString("bottle_address", "") ?: ""
        set(value) = store.edit().putString("bottle_address", value).apply()

    var bottleName: String
        get() = store.getString("bottle_name", "") ?: ""
        set(value) = store.edit().putString("bottle_name", value).apply()

    var periodic: Boolean
        get() = store.getBoolean("periodic", false)
        set(value) = store.edit().putBoolean("periodic", value).apply()

    val log: String
        get() = store.getString("log", "") ?: ""

    @Synchronized
    fun note(result: String) {
        val stamp = SimpleDateFormat("EEE HH:mm", Locale.getDefault()).format(Date())
        val lines = listOf("$stamp  $result") + log.lines().filter { it.isNotBlank() }
        store.edit().putString("log", lines.take(LOG_LINES).joinToString("\n")).apply()
    }

    val ready: Boolean
        get() = url.isNotBlank() && bottleAddress.isNotBlank()

    companion object {
        private const val LOG_LINES = 40
    }
}

class HttpHome(private val url: String) : Home {
    override fun post(body: JSONObject): Reply {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 30_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = connection.responseCode
            val stream = if (code < 400) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() } ?: ""
            val parsed = try {
                JSONObject(text)
            } catch (err: JSONException) {
                null
            }
            return Reply(code, parsed)
        } finally {
            connection.disconnect()
        }
    }
}

object Syncer {
    private const val WORK_NAME = "relay"
    private const val SCAN_SECONDS = 30L
    private val running = ReentrantLock()

    val PERMISSIONS = arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)

    fun allowed(context: Context): Boolean =
        PERMISSIONS.all { context.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

    /** One sync, start to finish. The result is also added to the log. */
    fun run(context: Context): String {
        val app = context.applicationContext
        val settings = Settings(app)
        if (!running.tryLock()) return "A sync is already running"
        val result = try {
            when {
                !settings.ready -> app.getString(R.string.needs_setup)
                !allowed(app) -> app.getString(R.string.needs_permission)
                else -> Relay(
                    HttpHome(settings.url),
                    Outbox(app.filesDir),
                    connect = {
                        GattLink.scan(app, SCAN_SECONDS, settings.bottleAddress).firstOrNull()
                            ?.let { GattLink.open(app, it.device) }
                    },
                ).run()
            }
        } catch (err: Exception) {
            "Stopped: ${err.message ?: err.javaClass.simpleName}"
        } finally {
            running.unlock()
        }
        settings.note(result)
        return result
    }

    fun schedule(context: Context, on: Boolean) {
        val manager = WorkManager.getInstance(context.applicationContext)
        if (!on) {
            manager.cancelUniqueWork(WORK_NAME)
            return
        }
        val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
        val request = PeriodicWorkRequest.Builder(RelayWorker::class.java, 30, TimeUnit.MINUTES)
            .setConstraints(online)
            .build()
        manager.enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
    }
}

class RelayWorker(context: Context, parameters: WorkerParameters) : Worker(context, parameters) {
    override fun doWork(): Result {
        Syncer.run(applicationContext)
        return Result.success()
    }
}
