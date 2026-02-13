# Deflax Android TV Player

Small Android TV app that:

- plays a single Mux HLS stream
- downloads and displays XMLTV EPG data
- highlights the currently airing program

## Configure

Edit `app/src/main/java/net/deflax/tv/AppConfig.kt`:

- `STREAM_URL`: your mux playlist URL (example: `https://your-domain/live/stream.m3u8`)
- `EPG_URL`: your EPG endpoint (example: `https://your-domain/epg.xml`)
- `EPG_CHANNEL_ID`: optional channel filter from XMLTV; leave empty to show all

## Build

Open `android-tv-app` in Android Studio and run on an Android TV device or emulator.

If you use command line Gradle:

```bash
./gradlew :app:assembleDebug
```

## Release

- Release checklist: `PLAY_RELEASE_CHECKLIST.md`
- Play listing template: `PLAY_STORE_METADATA_TEMPLATE.md`

Before each upload, update `versionCode` and `versionName` in `app/build.gradle.kts`.
