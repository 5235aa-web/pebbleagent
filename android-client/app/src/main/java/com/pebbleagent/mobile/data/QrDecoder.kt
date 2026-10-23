package com.pebbleagent.mobile.data

import android.graphics.Bitmap
import androidx.camera.core.ImageProxy
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader

/**
 * Decodes a QR code from a camera frame or a still image.
 *
 * Every attempt builds its own reader. ZXing's `MultiFormatReader` keeps
 * per-decode state and is not thread-safe, so one shared instance can leave the
 * next caller with a half-reset reader that silently finds nothing — which looks
 * exactly like "no QR code here".
 *
 * Passes run cheapest-first and are independent: the plain QR reader with no
 * hints, a fresh multi-format reader with TRY_HARDER, then an explicitly
 * inverted image for a code drawn light-on-dark. All read the same ARGB pixel
 * array through `RGBLuminanceSource`, so a camera frame and a picked screenshot
 * follow exactly the same path.
 */
object QrDecoder {

    /** Decode from an image the user picked out of the gallery. */
    fun decode(bitmap: Bitmap): String? {
        if (bitmap.width <= 0 || bitmap.height <= 0) return null
        val scaled = bounded(bitmap, 1600)
        val passes = buildList {
            add(scaled)
            crop(scaled, 0.72f)?.let { add(it) }
            if (scaled !== bitmap) add(bitmap)
        }
        for (candidate in passes) {
            decodeOnce(candidate)?.let { return it }
        }
        return null
    }

    /** Decode from a live CameraX analysis frame. */
    fun decode(image: ImageProxy): String? {
        val bitmap = try {
            image.toBitmap()
        } catch (_: Exception) {
            return null
        }
        return decode(bitmap)
    }

    private fun decodeOnce(bitmap: Bitmap): String? {
        val w = bitmap.width
        val h = bitmap.height
        if (w <= 0 || h <= 0) return null
        val pixels = IntArray(w * h)
        try {
            bitmap.getPixels(pixels, 0, w, 0, 0, w, h)
        } catch (_: Exception) {
            return null
        }

        runCatching {
            return QRCodeReader()
                .decode(BinaryBitmap(HybridBinarizer(RGBLuminanceSource(w, h, pixels))))
                .text
        }

        runCatching {
            val reader = MultiFormatReader().apply {
                setHints(
                    mapOf(
                        DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
                        DecodeHintType.TRY_HARDER to true,
                    )
                )
            }
            return reader
                .decodeWithState(BinaryBitmap(HybridBinarizer(RGBLuminanceSource(w, h, pixels))))
                .text
        }

        runCatching {
            val inverted = IntArray(pixels.size) { i -> pixels[i] xor 0x00FFFFFF }
            return QRCodeReader()
                .decode(BinaryBitmap(HybridBinarizer(RGBLuminanceSource(w, h, inverted))))
                .text
        }

        return null
    }

    private fun bounded(src: Bitmap, max: Int): Bitmap {
        val longest = maxOf(src.width, src.height)
        if (longest <= max) return src
        val ratio = max.toFloat() / longest
        val w = (src.width * ratio).toInt().coerceAtLeast(1)
        val h = (src.height * ratio).toInt().coerceAtLeast(1)
        return runCatching { Bitmap.createScaledBitmap(src, w, h, true) }.getOrDefault(src)
    }

    private fun crop(src: Bitmap, fraction: Float): Bitmap? {
        val w = (src.width * fraction).toInt()
        val h = (src.height * fraction).toInt()
        if (w <= 0 || h <= 0) return null
        val x = (src.width - w) / 2
        val y = (src.height - h) / 2
        return runCatching { Bitmap.createBitmap(src, x, y, w, h) }.getOrNull()
    }
}
