package io.github.tech2urdoor.bottlerelay

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
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

        address.setText(settings.url)
        findViewById<Button>(R.id.save).setOnClickListener { saveAddress() }
        choose.setOnClickListener { chooseBottle() }
        sync.setOnClickListener { syncNow() }

        val periodic = findViewById<Switch>(R.id.periodic)
        periodic.isChecked = settings.periodic
        periodic.setOnCheckedChangeListener { _, on ->
            settings.periodic = on
            Syncer.schedule(this, on)
        }

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
    }

    private fun say(message: Int) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    private fun saveAddress() {
        val typed = address.text.toString().trim()
        if (!typed.startsWith("https://") && !typed.startsWith("http://")) {
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
            val found = try {
                GattLink.scan(this, 8)
            } catch (err: Exception) {
                emptyList()
            }
            // Some bottles pad the name they advertise with zero bytes.
            val names = found.map { (it.scanRecord?.deviceName ?: it.device.name ?: "h2o").trim('\u0000', ' ') }
            runOnUiThread {
                choose.isEnabled = true
                choose.setText(R.string.choose)
                if (isFinishing || isDestroyed) return@runOnUiThread
                if (found.isEmpty()) {
                    say(R.string.none_found)
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
