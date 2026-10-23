package com.pebbleagent.mobile.data

import android.net.Uri

/**
 * Everything a phone needs to reach one desktop, as packed into the QR code by
 * `lan_access.build_pairing_uri` on the backend:
 *
 *     pebble://pair?h=<host>&p=<port>&f=<64 hex>&c=<code>
 *
 * The fingerprint is emitted without separators to keep the payload short; it is
 * re-formatted for display and normalised before comparing.
 */
data class PairingTarget(
    val host: String,
    val port: Int,
    /** Lowercase hex, no separators, as carried in the URI. */
    val fingerprint: String,
    val code: String,
) {
    /** `AA:BB:...` form, matching what the desktop panel shows. */
    val displayFingerprint: String
        get() = fingerprint.chunked(2).joinToString(":").uppercase()

    val baseUrl: String
        get() = "wss://$host:$port"
}

sealed interface PairingParseResult {
    data class Ok(val target: PairingTarget) : PairingParseResult
    data class Failure(val reason: String) : PairingParseResult
}

private val HEX_64 = Regex("^[0-9a-fA-F]{32}([0-9a-fA-F]{32})?$")

fun parsePairingUri(raw: String): PairingParseResult {
    val text = raw.trim()
    if (text.isEmpty()) return PairingParseResult.Failure("Nothing to read yet")

    val uri = try {
        Uri.parse(text)
    } catch (e: Exception) {
        return PairingParseResult.Failure("Not a valid URI")
    }

    if (!uri.scheme.equals("pebble", ignoreCase = true) || uri.host != "pair") {
        return PairingParseResult.Failure("Expected a pebble://pair link")
    }

    val host = uri.getQueryParameter("h")?.trim().orEmpty()
    if (host.isEmpty()) return PairingParseResult.Failure("Pairing link has no host")

    val port = uri.getQueryParameter("p")?.toIntOrNull()
        ?: return PairingParseResult.Failure("Pairing link has no valid port")
    if (port !in 1..65535) return PairingParseResult.Failure("Port out of range: $port")

    val fingerprint = uri.getQueryParameter("f")?.trim().orEmpty()
    if (!HEX_64.matches(fingerprint)) {
        return PairingParseResult.Failure("Pairing link has a malformed certificate fingerprint")
    }

    val code = uri.getQueryParameter("c")?.trim().orEmpty()
    if (code.isEmpty()) return PairingParseResult.Failure("Pairing link has no pairing code")

    return PairingParseResult.Ok(
        PairingTarget(
            host = host,
            port = port,
            fingerprint = fingerprint.lowercase(),
            code = code,
        )
    )
}
