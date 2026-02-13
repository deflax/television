# Google Play Release Checklist (Android TV)

Use this checklist for every release of `net.deflax.tv`.

## 1) Update app version

- Edit `app/build.gradle.kts`
- Increment `versionCode` (must always go up)
- Set `versionName` (human-readable, for example `1.0.1`)

## 2) Verify runtime config

- Confirm production URLs in `app/src/main/java/net/deflax/tv/AppConfig.kt`
  - `STREAM_URL`
  - `EPG_URL`
  - `EPG_CHANNEL_ID` (optional)
- Confirm only HTTPS endpoints are used

## 3) TV UX sanity checks

- Launch from Android TV home screen
- App icon and TV banner render correctly
- Stream starts and recovers after temporary network loss
- EPG loads and updates
- Remote keys work:
  - OK/Enter/Info/Menu toggles overlay
  - Back restores overlay when hidden

## 4) Build signed App Bundle

- Android Studio: `Build` -> `Generate Signed Bundle / APK` -> `Android App Bundle`
- Use release keystore
- Keep keystore and passwords backed up securely

## 5) Upload to Play Console

- Create release in Internal testing first
- Upload `.aab`
- Add release notes
- Roll out and validate install/update from Play

## 6) Complete Play listing/compliance

- Store listing text (use `PLAY_STORE_METADATA_TEMPLATE.md`)
- App icon (512x512) for listing
- TV screenshots (16:9)
- Content rating questionnaire
- Data safety form
- Privacy policy URL

## 7) Promote release

- Internal -> Closed/Open testing (optional) -> Production
- Monitor Android Vitals / crashes after rollout
