plugins {
    id("com.android.application")
    // AGP 9 ships its own Kotlin Gradle Plugin, so org.jetbrains.kotlin.android
    // must not be applied here. Only the Compose compiler plugin is still needed.
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.pebbleagent.mobile"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.pebbleagent.mobile"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation("androidx.activity:activity-compose:1.13.0")

    // WebSocket transport. JSON is handled with org.json (platform built-in) to
    // avoid pulling in kotlinx-serialization and its compiler plugin.
    implementation("com.squareup.okhttp3:okhttp:5.5.1")

    // QR scanning. CameraX for the preview/analysis pipeline, ZXing core for
    // decoding. Deliberately not ML Kit: it needs Google Play services, which
    // many domestic devices do not ship.
    val camerax = "1.6.2"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")
    implementation("com.google.zxing:core:3.5.4")

    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")

    debugImplementation("androidx.compose.ui:ui-tooling")
}
