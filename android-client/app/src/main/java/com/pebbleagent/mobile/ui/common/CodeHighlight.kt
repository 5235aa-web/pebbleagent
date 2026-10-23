package com.pebbleagent.mobile.ui.common

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle

/**
 * Lightweight syntax highlighting for code blocks and diff rows.
 *
 * Deliberately a single-pass scanner instead of a parser: code arriving from a
 * gateway can be truncated mid-token (a stream that was interrupted), so a
 * tokenizer that requires balanced input would either throw or lose the tail.
 * Scanning one character at a time always terminates and always renders what it
 * was given.
 */

private enum class Kind { Plain, Keyword, Str, Comment, Number, Type, Func }

private val TOKEN_COLORS = mapOf(
    Kind.Keyword to Color(0xFFFFBF00),
    Kind.Str to Color(0xFF9CCC65),
    Kind.Comment to Color(0xFF6A6A6A),
    Kind.Number to Color(0xFF82B1FF),
    Kind.Type to Color(0xFF4DD0E1),
    Kind.Func to Color(0xFFFFE082),
)

private val PY = setOf(
    "def", "class", "return", "if", "elif", "else", "for", "while", "import", "from",
    "as", "try", "except", "finally", "with", "lambda", "yield", "async", "await",
    "pass", "break", "continue", "raise", "global", "nonlocal", "assert", "del",
    "in", "is", "not", "and", "or", "None", "True", "False", "self", "match", "case",
)

private val KT = setOf(
    "fun", "val", "var", "class", "object", "interface", "data", "sealed", "enum",
    "if", "else", "when", "for", "while", "do", "return", "import", "package",
    "private", "public", "internal", "protected", "override", "open", "abstract",
    "companion", "init", "constructor", "this", "super", "null", "true", "false",
    "is", "as", "in", "out", "by", "lateinit", "suspend", "inline", "reified",
    "try", "catch", "finally", "throw", "typealias", "where", "it",
)

private val JS = setOf(
    "function", "const", "let", "var", "class", "extends", "return", "if", "else",
    "for", "while", "do", "switch", "case", "default", "break", "continue", "new",
    "this", "super", "import", "export", "from", "as", "async", "await", "try",
    "catch", "finally", "throw", "typeof", "instanceof", "null", "undefined",
    "true", "false", "interface", "type", "enum", "implements", "public", "private",
    "protected", "readonly", "static", "yield", "of", "in", "void",
)

private val JAVA = setOf(
    "class", "interface", "enum", "extends", "implements", "public", "private",
    "protected", "static", "final", "void", "return", "if", "else", "for", "while",
    "do", "switch", "case", "default", "break", "continue", "new", "this", "super",
    "try", "catch", "finally", "throw", "throws", "import", "package", "null",
    "true", "false", "abstract", "synchronized", "volatile", "transient", "record",
)

private val RUST = setOf(
    "fn", "let", "mut", "const", "static", "struct", "enum", "trait", "impl", "mod",
    "use", "pub", "crate", "self", "super", "return", "if", "else", "match", "for",
    "while", "loop", "break", "continue", "move", "ref", "where", "async", "await",
    "dyn", "unsafe", "as", "in", "Some", "None", "Ok", "Err", "true", "false",
)

private val GO = setOf(
    "func", "package", "import", "var", "const", "type", "struct", "interface",
    "map", "chan", "go", "defer", "select", "switch", "case", "default", "return",
    "if", "else", "for", "range", "break", "continue", "fallthrough", "nil",
    "true", "false", "make", "new", "len", "cap", "append", "delete",
)

private val BASH = setOf(
    "if", "then", "elif", "else", "fi", "for", "in", "do", "done", "while", "until",
    "case", "esac", "function", "return", "export", "local", "readonly", "declare",
    "source", "echo", "cd", "exit", "set", "unset", "shift", "trap", "eval",
)

private val SQL = setOf(
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE", "CREATE", "TABLE", "INDEX", "ALTER", "DROP", "JOIN", "LEFT", "RIGHT",
    "INNER", "OUTER", "ON", "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "OFFSET",
    "AND", "OR", "NOT", "NULL", "AS", "DISTINCT", "COUNT", "SUM", "AVG", "MIN",
    "MAX", "CASE", "WHEN", "THEN", "ELSE", "END", "PRIMARY", "KEY", "FOREIGN",
)

private val SWIFT = setOf(
    "func", "let", "var", "class", "struct", "enum", "protocol", "extension",
    "return", "if", "else", "guard", "for", "in", "while", "switch", "case",
    "default", "break", "continue", "import", "public", "private", "internal",
    "static", "init", "self", "super", "nil", "true", "false", "try", "catch",
)

private val C = setOf(
    "int", "char", "float", "double", "void", "struct", "union", "enum", "typedef",
    "static", "const", "extern", "return", "if", "else", "for", "while", "do",
    "switch", "case", "default", "break", "continue", "sizeof", "include", "define",
    "NULL", "unsigned", "signed", "long", "short", "class", "public", "private",
    "protected", "namespace", "template", "typename", "new", "delete", "this",
)

private val KEYWORDS: Map<String, Set<String>> = mapOf(
    "python" to PY,
    "kotlin" to KT,
    "javascript" to JS,
    "java" to JAVA,
    "rust" to RUST,
    "go" to GO,
    "bash" to BASH,
    "sql" to SQL,
    "swift" to SWIFT,
    "c" to C,
)

/** Line-comment marker per language; absent means the language has none. */
private val LINE_COMMENT = mapOf(
    "python" to "#",
    "bash" to "#",
    "yaml" to "#",
    "kotlin" to "//",
    "javascript" to "//",
    "java" to "//",
    "rust" to "//",
    "go" to "//",
    "swift" to "//",
    "c" to "//",
    "sql" to "--",
)

private fun normalizeLang(raw: String): String {
    val l = raw.trim().lowercase()
    return when (l) {
        "py", "python", "python3" -> "python"
        "kt", "kotlin", "kts" -> "kotlin"
        "js", "javascript", "jsx", "mjs", "cjs", "ts", "typescript", "tsx" -> "javascript"
        "java" -> "java"
        "rs", "rust" -> "rust"
        "go", "golang" -> "go"
        "sh", "bash", "shell", "zsh", "console" -> "bash"
        "json", "jsonc" -> "json"
        "sql" -> "sql"
        "yaml", "yml" -> "yaml"
        "swift" -> "swift"
        "c", "h", "cpp", "c++", "cc", "hpp", "cxx" -> "c"
        else -> l
    }
}

/** Maps a file extension to a language name for [highlightCode]. */
fun languageForPath(path: String): String {
    val name = path.substringAfterLast('/').substringAfterLast('\\')
    val ext = name.substringAfterLast('.', "").lowercase()
    return when (ext) {
        "py" -> "python"
        "kt", "kts" -> "kotlin"
        "js", "jsx", "mjs", "cjs" -> "javascript"
        "ts", "tsx" -> "javascript"
        "java" -> "java"
        "rs" -> "rust"
        "go" -> "go"
        "sh", "bash" -> "bash"
        "json" -> "json"
        "sql" -> "sql"
        "yml", "yaml" -> "yaml"
        "swift" -> "swift"
        "c", "h", "cpp", "cc", "hpp" -> "c"
        else -> ext
    }
}

fun highlightCode(code: String, language: String = ""): AnnotatedString = buildAnnotatedString {
    val lang = normalizeLang(language)
    val keywords = KEYWORDS[lang].orEmpty()
    val lineToken = LINE_COMMENT[lang]
    val n = code.length
    var i = 0

    while (i < n) {
        val c = code[i]
        val start = i
        var kind = Kind.Plain
        var end = i

        when {
            lineToken != null && code.startsWith(lineToken, i) -> {
                end = code.indexOf('\n', i).let { if (it < 0) n else it }
                kind = Kind.Comment
            }

            code.startsWith("/*", i) -> {
                end = code.indexOf("*/", i + 2).let { if (it < 0) n else it + 2 }
                kind = Kind.Comment
            }

            c == '"' || c == '\'' || c == '`' -> {
                val triple = i + 2 < n && code[i + 1] == c && code[i + 2] == c
                val quote = if (triple) "$c$c$c" else "$c"
                var j = i + quote.length
                while (j < n) {
                    if (code[j] == '\\' && j + 1 < n) {
                        j += 2
                        continue
                    }
                    if (code.startsWith(quote, j)) {
                        j += quote.length
                        break
                    }
                    j++
                }
                end = if (j > n) n else j
                kind = Kind.Str
            }

            c.isDigit() -> {
                var j = i
                while (j < n && (code[j].isLetterOrDigit() || code[j] == '.' || code[j] == '_')) j++
                end = j
                kind = Kind.Number
            }

            c.isLetter() || c == '_' -> {
                var j = i
                while (j < n && (code[j].isLetterOrDigit() || code[j] == '_')) j++
                val word = code.substring(i, j)
                kind = when {
                    word in keywords -> Kind.Keyword
                    word[0].isUpperCase() -> Kind.Type
                    j < n && code[j] == '(' -> Kind.Func
                    else -> Kind.Plain
                }
                end = j
            }

            else -> end = i + 1
        }

        // A zero-width step would spin forever on malformed input.
        if (end <= start) end = start + 1

        val segment = code.substring(start, end)
        if (kind == Kind.Plain) {
            append(segment)
        } else {
            withStyle(SpanStyle(color = TOKEN_COLORS.getValue(kind))) { append(segment) }
        }
        i = end
    }
}
