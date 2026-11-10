package io.github.tech2urdoor.bottlerelay

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private lateinit var settings: Settings
    private lateinit var address: EditText
    private lateinit var bottle: TextView
    private lateinit var log: TextView
    private lateinit var sync: Button
    private lateinit var choose: Button
    private lateinit var background: TextView
    private lateinit var allowBackground: Button

    @SuppressLint("UseSwitchCompatOrMaterialCode")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        settings = Settings(this)
        address = findViewById(R.id.address)
        bottle = findViewById(R.id.bottle)
        log = findViewById(R.id.log)
        sync = findViewById(R.id.sync)
        choose = findViewById(R.id.choose)
        background = findViewById(R.id.background)
        allowBackground = findViewById(R.id.allow_background)
        allowBackground.setOnClickListener { askToRunInBackground() }

        address.setText(settings.url)
        findViewById<Button>(R.id.save).setOnClickListener { saveAddress() }
        choose.setOnClickListener { chooseBottle() }
        sync.setOnClickListener { syncNow() }

        val periodic = findViewById<Switch>(R.id.periodic)
        periodic.isChecked = settings.periodic
        periodic.setOnCheckedChangeListener { _, on ->
            settings.periodic = on
            Syncer.schedule(this, on)
            if (on && !runsInBackground()) askToRunInBackground()
        }

        // Asked again on every open, so a schedule lost to a reinstall or a restore comes back.
        if (settings.periodic) Syncer.schedule(this, true)

        if (!Syncer.allowed(this)) requestPermissions(Syncer.PERMISSIONS, 0)
    }

    override fun onResume() {
        super.onResume()
        show()
    }

    private fun show() {
        bottle.text = if (settings.bottleAddress.isBlank()) {
            getString(R.string.bottle_none)
        } else {
            getString(R.string.bottle_chosen, settings.bottleName.ifBlank { "h2o" })
        }
        log.text = settings.log
        val free = runsInBackground()
        background.setText(if (free) R.string.background_allowed else R.string.background_held)
        allowBackground.visibility = if (free) View.GONE else View.VISIBLE
    }

    /** True once Android has agreed not to hold this app's background work back. */
    private fun runsInBackground(): Boolean =
        getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

    @SuppressLint("BatteryLife")
    private fun askToRunInBackground() {
        try {
            startActivity(
                Intent(
                    android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (err: Exception) {
            startActivity(Intent(android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }

    private fun say(message: Int) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    private fun saveAddress() {
        val typed = address.text.toString().trim()
        if (!Relay.safeAddress(typed)) {
            say(R.string.bad_address)
            return
        }
        settings.url = typed
        say(R.string.saved)
    }

    @SuppressLint("MissingPermission")
    private fun chooseBottle() {
        if (!Syncer.allowed(this)) {
            requestPermissions(Syncer.PERMISSIONS, 0)
            say(R.string.needs_permission)
            return
        }
        choose.isEnabled = false
        choose.setText(R.string.choosing)
        thread {
            var problem: String? = null
            val found = try {
                GattLink.scan(this, 8)
            } catch (err: Exception) {
                problem = err.message ?: err.javaClass.simpleName
                emptyList()
            }
            // Some bottles pad the name they advertise with zero bytes.
            val names = found.map { (it.scanRecord?.deviceName ?: it.device.name ?: "h2o").trim('\u0000', ' ') }
            runOnUiThread {
                choose.isEnabled = true
                choose.setText(R.string.choose)
                if (isFinishing || isDestroyed) return@runOnUiThread
                if (found.isEmpty()) {
                    // Bluetooth being off, or a scan Android refused, isn't a bottle out of earshot.
                    if (problem != null) Toast.makeText(this, problem, Toast.LENGTH_LONG).show() else say(R.string.none_found)
                    return@runOnUiThread
                }
                AlertDialog.Builder(this)
                    .setTitle(R.string.pick_title)
                    .setItems(names.toTypedArray()) { _, which ->
                        settings.bottleAddress = found[which].device.address
                        settings.bottleName = names[which]
                        show()
                    }
                    .show()
            }
        }
    }

    private fun syncNow() {
        sync.isEnabled = false
        sync.setText(R.string.syncing)
        thread {
            Syncer.run(this)
            runOnUiThread {
                sync.isEnabled = true
                sync.setText(R.string.sync_now)
                show()
            }
        }
    }
}
