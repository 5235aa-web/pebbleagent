package com.pebbleagent.mobile.ui.theme

import androidx.compose.ui.graphics.Color

/**
 * Palette transcribed verbatim from the prototype's `:root` and inline styles.
 *
 * Two things are easy to get wrong here and were wrong before:
 * `--muted` is a dark gold (#CC9B1F), not a grey; the greys in the design are
 * written out literally (#6f6f6f, #7a7a7a, #5f5f5f) and are a separate axis.
 * Keeping them under the names the CSS uses makes the translation auditable.
 */
object PebbleColors {
    // --- :root ---
    val Primary = Color(0xFFFFD700)      // --primary
    val Accent = Color(0xFFFFBF00)       // --accent
    val BorderGold = Color(0xFFCD7F32)   // --border-gold

    val Text = Color(0xFFFFF8DC)         // --text
    val Muted = Color(0xFFCC9B1F)        // --muted  (dark gold, NOT grey)
    val TextMuted = Muted
    val Label = Color(0xFFDAA520)        // --label
    val TextBody = Color(0xFFD4D4D4)     // --text-body

    val BgDeep = Color(0xFF0D0D0D)       // --bg-deep
    val BgSurface = Color(0xFF111111)    // --bg-surface
    val BgCard = Color(0xFF1A1A1A)       // --bg-card
    val BgHover = Color(0xFF222222)      // --bg-hover
    val BgInput = Color(0xFF0A0A0A)      // --bg-input

    val BorderSubtle = Color(0xFF2A2A2A) // --border-subtle
    val BorderHover = Color(0xFF3A3A3A)  // --border-hover

    val Success = Color(0xFF4CAF50)      // --success
    val Error = Color(0xFFEF5350)        // --error
    val Warn = Color(0xFFFFA726)         // --warn
    val Info = Color(0xFF44AAFF)         // --info

    // --- greys written literally in rules, not in :root ---
    val Grey6F = Color(0xFF6F6F6F)       // .row-main span, .tool-dur, .sess-time
    val Grey7A = Color(0xFF7A7A7A)       // .kpi-sub, .tool-mini, .inp.dim
    val Grey5F = Color(0xFF5F5F5F)       // .tstamp, .ph, .empty, .inbox-top .t
    /** Alias kept for call sites that predate the literal-grey naming. */
    val TextDim = Grey5F
    val GreyB9 = Color(0xFFB9B9B9)       // .think-h b, .dl .tx
    val Grey4A = Color(0xFF4A4A4A)       // .dl .ln, .opt-mark border
    val Grey3A = Color(0xFF3A3A3A)       // .dl.ctx .sign, .grab
    val Grey33 = Color(0xFF333333)       // .sheet-grip
    val Grey8A = Color(0xFF8A8A8A)       // .opt-main span, .pair-top span

    // --- semantic colours that appear as literals in diff / status rules ---
    val Green6F = Color(0xFF6FCE73)      // .tool-ico.ok, .sum-num .add
    val RedFF8A = Color(0xFFFF8A88)      // .tool-ico.hot, .btn-deny, .conn-err
    val Blue6C = Color(0xFF6CB8FF)       // .tool-ico.run
    val WarnD9 = Color(0xFFD9A860)       // .hint text
    val WarnFFC1 = Color(0xFFFFC16B)     // .conn-warn text

    // --- code highlighting ---
    val Kw = Color(0xFFFF9D5C)
    val Fn = Color(0xFF8FD0FF)
    val Str = Color(0xFFA8D98A)
    val Cmt = Color(0xFF5F5F5F)
    val CodeText = Color(0xFFCFCFCF)
    val CodeBg = Color(0xFF080808)
}
