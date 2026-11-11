package io.github.tech2urdoor.bottlerelay

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.ParcelUuid
import java.io.IOException
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Android's Bluetooth answers through callbacks on its own threads. This turns each call into one
 * that waits for its answer, so the relay reads top to bottom. Callers hold the Bluetooth permissions.
 */
@SuppressLint("MissingPermission")
class GattLink private constructor() : Link {
    private class Answer(val kind: String, val status: Int, val value: ByteArray? = null)

    private val answers = LinkedBlockingQueue<Answer>()
    private val notifications = LinkedBlockingQueue<Notification>()
    private lateinit var gatt: BluetoothGatt

    @Volatile
    private var gone = false

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                answers.put(Answer(CONNECTED, status))
            } else {
                gone = true
                answers.put(Answer(GONE, status))
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            answers.put(Answer(SERVICES, status))
        }

        override fun onCharacteristicRead(
            gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray, status: Int,
        ) {
            answers.put(Answer(READ, status, value))
        }

        override fun onCharacteristicWrite(
            gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int,
        ) {
            answers.put(Answer(WRITE, status))
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            answers.put(Answer(DESCRIPTOR, status))
        }

        override fun onCharacteristicChanged(
            gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray,
        ) {
            notifications.put(Notification(value, System.currentTimeMillis() / 1000.0))
        }
    }

    private fun await(kind: String, seconds: Long = ANSWER_SECONDS): Answer {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds)
        while (true) {
            if (gone) throw IOException("the bottle disconnected")
            val answer = answers.poll(deadline - System.nanoTime(), TimeUnit.NANOSECONDS)
                ?: throw IOException("the bottle didn't answer in $seconds seconds")
            if (answer.kind == GONE) throw IOException("the bottle disconnected (${answer.status})")
            if (answer.kind != kind) continue
            if (answer.status != BluetoothGatt.GATT_SUCCESS) throw IOException("the bottle refused (${answer.status})")
            return answer
        }
    }

    private fun find(characteristic: UUID): BluetoothGattCharacteristic =
        gatt.services.firstNotNullOfOrNull { it.getCharacteristic(characteristic) }
            ?: throw IOException("the bottle has no $characteristic")

    /**
     * Before each request, drops answers nobody is waiting for any more. One that arrives after its
     * request timed out would otherwise be taken for the answer to the next request of the same kind.
     */
    private fun fresh() = answers.clear()

    override fun read(characteristic: UUID): ByteArray {
        val source = find(characteristic)
        fresh()
        if (!gatt.readCharacteristic(source)) throw IOException("the read couldn't start")
        return await(READ).value ?: ByteArray(0)
    }

    override fun write(characteristic: UUID, value: ByteArray) {
        val target = find(characteristic)
        val acknowledged = target.properties and BluetoothGattCharacteristic.PROPERTY_WRITE != 0
        val type = if (acknowledged) {
            BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        } else {
            BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        }
        fresh()
        val started = gatt.writeCharacteristic(target, value, type)
        if (started != BluetoothStatusCodes.SUCCESS) throw IOException("the write couldn't start ($started)")
        await(WRITE)
    }

    override fun subscribe(characteristic: UUID) {
        val source = find(characteristic)
        if (!gatt.setCharacteristicNotification(source, true)) throw IOException("notifications couldn't be turned on")
        val descriptor = source.getDescriptor(CLIENT_CONFIGURATION)
            ?: throw IOException("the bottle has nowhere to turn notifications on")
        val notifies = source.properties and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0
        val value = if (notifies) {
            BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        } else {
            BluetoothGattDescriptor.ENABLE_INDICATION_VALUE
        }
        fresh()
        val started = gatt.writeDescriptor(descriptor, value)
        if (started != BluetoothStatusCodes.SUCCESS) throw IOException("notifications couldn't be turned on ($started)")
        await(DESCRIPTOR)
    }

    override fun next(seconds: Double): Notification? {
        val deadline = System.nanoTime() + (seconds * 1e9).toLong()
        // In short waits, so a bottle that has gone is noticed without sitting out the whole time.
        while (true) {
            val left = deadline - System.nanoTime()
            if (left <= 0) return null
            notifications.poll(minOf(left, TimeUnit.MILLISECONDS.toNanos(250)), TimeUnit.NANOSECONDS)?.let { return it }
            if (gone && notifications.isEmpty()) throw IOException("the bottle disconnected")
        }
    }

    override fun close() {
        if (!::gatt.isInitialized) return
        try {
            gatt.disconnect()
        } finally {
            gatt.close()
        }
    }

    companion object {
        private const val CONNECTED = "connected"
        private const val GONE = "gone"
        private const val SERVICES = "services"
        private const val READ = "read"
        private const val WRITE = "write"
        private const val DESCRIPTOR = "descriptor"
        private const val ANSWER_SECONDS = 15L
        private const val CONNECT_SECONDS = 30L
        private val CLIENT_CONFIGURATION: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        fun open(context: Context, device: BluetoothDevice): GattLink {
            val link = GattLink()
            link.gatt = device.connectGatt(context, false, link.callback, BluetoothDevice.TRANSPORT_LE)
                ?: throw IOException("Bluetooth wouldn't start a connection")
            try {
                link.await(CONNECTED, CONNECT_SECONDS)
                if (!link.gatt.discoverServices()) throw IOException("the bottle's services couldn't be looked up")
                link.await(SERVICES)
            } catch (err: Exception) {
                // Any failure, a withdrawn permission included, or the connection is left open.
                link.close()
                throw err
            }
            return link
        }

        /**
         * Hands over the signal strength of each advert from one bottle, until the time is up or
         * `stopped` says so. For walking towards it after Find.
         */
        fun follow(context: Context, seconds: Long, address: String, stopped: () -> Boolean, heard: (Int) -> Unit) {
            val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            if (adapter == null || !adapter.isEnabled) throw IOException("Bluetooth is off")
            val scanner = adapter.bluetoothLeScanner ?: throw IOException("Bluetooth is off")
            val failure = AtomicInteger(0)
            val listener = object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) = heard(result.rssi)

                override fun onScanFailed(errorCode: Int) {
                    failure.set(errorCode)
                }
            }
            val filter = ScanFilter.Builder().setDeviceAddress(address).build()
            val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
            scanner.startScan(listOf(filter), settings, listener)
            try {
                val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds)
                while (System.nanoTime() < deadline && !stopped()) {
                    if (failure.get() != 0) throw IOException("Android wouldn't scan for the bottle (${failure.get()})")
                    Thread.sleep(250)
                }
            } finally {
                scanner.stopScan(listener)
            }
        }

        /** Listens for bottles. With an address, stops at the first advert from that one. */
        fun scan(context: Context, seconds: Long, address: String? = null): List<ScanResult> {
            val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
            if (adapter == null || !adapter.isEnabled) throw IOException("Bluetooth is off")
            val scanner = adapter.bluetoothLeScanner ?: throw IOException("Bluetooth is off")
            val heard = LinkedBlockingQueue<ScanResult>()
            val failure = AtomicInteger(0)
            val listener = object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) {
                    heard.offer(result)
                }

                override fun onScanFailed(errorCode: Int) {
                    failure.set(errorCode)
                }
            }
            // A scan Android wouldn't start hears nothing, which isn't the same as no bottle nearby.
            fun check() {
                if (failure.get() != 0) throw IOException("Android wouldn't scan for the bottle (${failure.get()})")
            }
            val filter = ScanFilter.Builder()
            if (address != null) filter.setDeviceAddress(address) else filter.setServiceUuid(ParcelUuid(Relay.SERVICE))
            val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
            scanner.startScan(listOf(filter.build()), settings, listener)
            try {
                if (address != null) {
                    val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds)
                    while (true) {
                        check()
                        val left = deadline - System.nanoTime()
                        if (left <= 0) return emptyList()
                        heard.poll(minOf(left, TimeUnit.MILLISECONDS.toNanos(250)), TimeUnit.NANOSECONDS)?.let { return listOf(it) }
                    }
                }
                Thread.sleep(TimeUnit.SECONDS.toMillis(seconds))
                check()
                return heard.distinctBy { it.device.address }
            } finally {
                scanner.stopScan(listener)
            }
        }
    }
}
