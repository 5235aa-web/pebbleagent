package com.pebbleagent.mobile.ui.common

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Renders the prototype's icons from their original SVG path data.
 *
 * The design uses 24x24 stroke icons (round caps/joins, no fill). Material's
 * `Icons.Filled.*` are solid glyphs with different proportions, and substituting
 * them was the single biggest reason the app did not look like the mockup. Path
 * strings are copied verbatim from the HTML so stroke weight and geometry match.
 */
@Composable
fun PebbleIcon(
    paths: List<String>,
    tint: Color,
    modifier: Modifier = Modifier,
    size: Dp = 24.dp,
    strokeWidth: Float = 1.7f,
    filled: Boolean = false,
) {
    Canvas(modifier = modifier.size(size)) {
        val scaleFactor = this.size.minDimension / 24f
        scale(scaleFactor, scaleFactor, pivot = androidx.compose.ui.geometry.Offset.Zero) {
            paths.forEach { d ->
                val path = runCatching {
                    PathParser().parsePathString(d).toPath()
                }.getOrNull() ?: return@forEach
                drawPath(
                    path = path,
                    color = tint,
                    style = if (filled) {
                        androidx.compose.ui.graphics.drawscope.Fill
                    } else {
                        Stroke(
                            width = strokeWidth,
                            cap = StrokeCap.Round,
                            join = StrokeJoin.Round,
                        )
                    },
                )
            }
        }
    }
}

/** Draws pre-filled circles, for the "more" glyph which is three dots. */
@Composable
fun PebbleDots(tint: Color, size: Dp = 24.dp, radius: Float = 1.6f) {
    Canvas(modifier = Modifier.size(size)) {
        val s = this.size.minDimension / 24f
        listOf(5f, 12f, 19f).forEach { cy ->
            drawCircle(
                color = tint,
                radius = radius * s,
                center = androidx.compose.ui.geometry.Offset(12f * s, cy * s),
            )
        }
    }
}

/** Path strings lifted from the prototype markup. */
object PebbleIcons {
    val Back = listOf("m15 18-6-6 6-6")
    val ChevronRight = listOf("m9 6 6 6-6 6")
    val ChevronDown = listOf("m6 9 6 6 6-6")
    val ChevronUp = listOf("m18 15-6-6-6 6")
    val Check = listOf("m5 12 5 5 9-9")
    val Search = listOf(
        "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z",
        "m20 20-3.5-3.5",
    )
    val Refresh = listOf("M21 12a9 9 0 1 1-2.6-6.4", "M21 4v5h-5")
    val ShieldAlert = listOf(
        "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
        "M12 8v4M12 16h.01",
    )
    val ShieldCheck = listOf(
        "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
        "m9 12 2 2 4-4",
    )
    val Terminal = listOf("m4 17 6-6-6-6", "M12 19h8")
    val Question = listOf("M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4", "M12 17h.01")
    val Laptop = listOf(
        "M4 4h16a2 2 0 0 1 2 2v11H2V6a2 2 0 0 1 2-2z",
        "M8 21h8M12 17v4",
    )
    val Folder = listOf("M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z")
    val Gear = listOf(
        "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
        "M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1",
    )
    val Expand = listOf(
        "M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4",
    )
    val Info = listOf("M12 9v4m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z")
    val Signal = listOf(
        "M5 12.5a10 10 0 0 1 14 0",
        "M8.5 16a5.5 5.5 0 0 1 7 0",
        "M11 19a1 1 0 1 0 2 0 1 1 0 0 0-2 0z",
    )
    val Attach = listOf(
        "M21.4 11.05 12.25 20.2a5.5 5.5 0 0 1-7.78-7.78l9.2-9.19a3.7 3.7 0 1 1 5.23 5.23l-9.2 9.2a1.85 1.85 0 1 1-2.61-2.62l8.49-8.48",
    )
    val Qr = listOf(
        "M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z",
        "M14 14h3v3h-3z", "M20 14h1M14 20h7",
    )
    val Plus = listOf("M12 5v14M5 12h14")

    // bottom navigation
    val NavSessions = listOf(
        "M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z",
        "M9 11h6M9 15h3",
    )
    val NavInbox = listOf(
        "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
        "m9 12 2 2 4-4",
    )
    val NavChanges = listOf(
        "M8.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
        "M8.5 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
        "M20.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
        "M6 8.5v7M8.5 6H15a3 3 0 0 1 3 3v.5",
    )
    val NavUsage = listOf("M3 3v18h18", "M7 16v2M12 11v7M17 6v12")
    val NavSettings = listOf(
        "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
        "M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z",
    )
}
