package com.pebbleagent.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import com.pebbleagent.mobile.ui.i18n.FontScale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.sp

/**
 * Accent tones offered in Settings > Appearance.
 *
 * Each tone carries three related colours rather than one: the prototype paints
 * emphasis with `primary` (icons, active nav), text/glow with `accent`, and
 * hairlines with `border`. Deriving the last two from the first with alpha
 * would look muddy on a near-black background, so they are chosen by hand.
 */
@Immutable
data class AccentTone(
    val key: String,
    val label: String,
    val primary: Color,
    val accent: Color,
    val border: Color,
)

val AccentTones = listOf(
    AccentTone("gold", "Gold", Color(0xFFFFD700), Color(0xFFFFBF00), Color(0xFFCD7F32)),
    AccentTone("jade", "Jade", Color(0xFF4FC08D), Color(0xFF3FA87A), Color(0xFF2E7D5B)),
    AccentTone("sky", "Sky", Color(0xFF6CB8FF), Color(0xFF4F9FE8), Color(0xFF3A7BB8)),
    AccentTone("violet", "Violet", Color(0xFFC77DFF), Color(0xFFA85FE0), Color(0xFF7B45A8)),
    AccentTone("coral", "Coral", Color(0xFFFF8A88), Color(0xFFE86D6B), Color(0xFFB85250)),
)

fun accentToneFor(key: String?): AccentTone =
    AccentTones.firstOrNull { it.key == key } ?: AccentTones.first()

val LocalAccentTone = staticCompositionLocalOf { AccentTones.first() }

/**
 * Read the active tone from composition.
 *
 * Separate from [PebbleColors] because those are constants; these three must
 * follow the user's Appearance choice. Anything that paints emphasis should
 * read from here, not from the fixed palette.
 */
object Palette {
    val primary: Color
        @Composable @ReadOnlyComposable get() = LocalAccentTone.current.primary

    val accent: Color
        @Composable @ReadOnlyComposable get() = LocalAccentTone.current.accent

    val border: Color
        @Composable @ReadOnlyComposable get() = LocalAccentTone.current.border
}

/** Monospace is the house font; the prototype uses it for every numeric/label slot. */
val Mono = FontFamily.Monospace

private val PebbleTypography = Typography(
    titleLarge = TextStyle(fontFamily = Mono, fontWeight = FontWeight.SemiBold, fontSize = 20.sp, letterSpacing = 0.2.sp),
    titleMedium = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 14.sp, letterSpacing = 0.2.sp),
    bodyLarge = TextStyle(fontFamily = Mono, fontSize = 13.sp, lineHeight = 20.sp),
    bodyMedium = TextStyle(fontFamily = Mono, fontSize = 12.sp, lineHeight = 18.sp),
    bodySmall = TextStyle(fontFamily = Mono, fontSize = 11.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontFamily = Mono, fontWeight = FontWeight.Medium, fontSize = 12.sp, letterSpacing = 0.3.sp),
    labelMedium = TextStyle(fontFamily = Mono, fontSize = 10.sp, letterSpacing = 0.5.sp),
    labelSmall = TextStyle(fontFamily = Mono, fontSize = 9.sp, letterSpacing = 0.6.sp),
)

/**
 * Dark-only by design: the prototype is a terminal UI and has no light variant,
 * so `isSystemInDarkTheme()` is intentionally ignored.
 */
@Composable
fun PebbleTheme(
    tone: AccentTone = AccentTones.first(),
    content: @Composable () -> Unit,
) {
    val scheme = darkColorScheme(
        primary = tone.primary,
        onPrimary = PebbleColors.BgDeep,
        primaryContainer = tone.border,
        onPrimaryContainer = PebbleColors.Text,
        secondary = tone.accent,
        onSecondary = PebbleColors.BgDeep,
        background = PebbleColors.BgDeep,
        onBackground = PebbleColors.TextBody,
        surface = PebbleColors.BgSurface,
        onSurface = PebbleColors.TextBody,
        surfaceVariant = PebbleColors.BgCard,
        onSurfaceVariant = PebbleColors.Muted,
        outline = PebbleColors.BorderSubtle,
        outlineVariant = PebbleColors.BorderHover,
        error = PebbleColors.Error,
        onError = PebbleColors.BgDeep,
    )
    CompositionLocalProvider(LocalAccentTone provides tone) {
        // Every `sp` size resolves through the density, so scaling fontScale
        // here resizes text across the whole app, including inside cards and
        // sheets, without touching a single layout constant.
        val base = LocalDensity.current
        CompositionLocalProvider(
            LocalDensity provides Density(base.density, base.fontScale * FontScale.value)
        ) {
            MaterialTheme(
                colorScheme = scheme,
                typography = PebbleTypography,
                content = content,
            )
        }
    }
}
