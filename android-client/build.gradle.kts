plugins {
    id("com.android.application") version "9.4.0" apply false
    // Must match the Kotlin Gradle Plugin version that AGP 9.4.0 bundles (2.2.10);
    // a newer compose plugin would drag in a different KGP and fail at apply time.
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.10" apply false
}
