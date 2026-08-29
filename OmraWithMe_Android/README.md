# Umrah Connect — Android App

A native Android WebView wrapper (`com.umrahconnect.app`) around the **Umrah Connect** web app.
The app loads the web app from a server URL baked in at build time, keeps navigation in-app,
supports deep links (`umrahconnect://...`), swipe-to-refresh, file uploads (profile pictures via
the system picker), and shows a bilingual (EN/AR) offline page when the server is unreachable.

## Build a debug APK

From this directory (Windows):

```
gradlew.bat assembleDebug
```

The APK lands in `app/build/outputs/apk/debug/app-debug.apk`.
Debug builds point at `http://10.0.2.2:8000` by default — the Android emulator's alias for
`localhost` on your machine, so run the web app server locally and launch the app in an emulator.

## Point a debug build at a LAN server (physical device)

Set `OMRA_DEBUG_SERVER_URL` in `gradle.properties` (project or `~/.gradle/gradle.properties`):

```
OMRA_DEBUG_SERVER_URL=http://192.168.1.42:8000
```

Then rebuild. Note: cleartext HTTP is only allowed to `10.0.2.2`, `localhost`, and `127.0.0.1`
(see `app/src/main/res/xml/network_security_config.xml`). For a LAN IP over plain HTTP, add a
matching `<domain>` entry to that file's `<domain-config>` for local testing.

## Release builds

Release builds **require** setting the real HTTPS production URL:

```
OMRA_RELEASE_SERVER_URL=https://your-real-production-domain.com
```

in `gradle.properties` before building (`gradlew.bat assembleRelease`). Release builds are
HTTPS-only: cleartext traffic and mixed content are blocked. You must also add a **signing
config** (upload keystore) to `app/build.gradle` before publishing to Google Play — an unsigned
or debug-signed release APK cannot be published.

Versioning lives in `app/build.gradle` (`versionCode` / `versionName`) — bump `versionCode`
on every Play Store upload.

Release signing is read from `gradle.properties` (project or `~/.gradle/gradle.properties`) and
only applied when **all four** properties are present (debug builds work without them):

```
OMRA_KEYSTORE_FILE=C:/keys/umrah-upload.keystore
OMRA_KEYSTORE_PASSWORD=...
OMRA_KEY_ALIAS=umrah
OMRA_KEY_PASSWORD=...
```

Release builds are minified and resource-shrunk (R8); keep `app/build/outputs/mapping/release/mapping.txt`
from each release for crash de-obfuscation.

## Play Console checklist

Before (or while) creating the Play listing, have the following ready.

### Upload keystore

Generate once and keep it safe (losing it means losing the ability to update the app,
unless you enroll in Play App Signing — recommended):

```
keytool -genkeypair -v -keystore umrah-upload.keystore -alias umrah -keyalg RSA -keysize 2048 -validity 10000
```

Then set the four `OMRA_KEYSTORE_*` / `OMRA_KEY_*` properties shown above.

### Data Safety form (App content → Data safety)

Declare that the app **collects**:

- Name, email address, phone number
- National ID / passport number (**optional**, user-entered)
- Messages (in-app chat)

And declare:

- Data is **shared between users** after a request is accepted (that is the app's purpose)
- All data is **encrypted in transit** (HTTPS-only release builds)
- **Account deletion is available in-app** at `/profile` (Danger Zone) and via the web URL
  `{domain}/profile`

### URLs to fill in

- **Account deletion URL**: `{domain}/profile`
- **Privacy policy URL**: `{domain}/privacy`

(replace `{domain}` with the production domain, i.e. the value of `OMRA_RELEASE_SERVER_URL`).

### Listing assets — still missing

- [ ] App icon export at **512×512 px** (PNG)
- [ ] **Feature graphic** 1024×500 px
- [ ] At least **2 English** phone screenshots
- [ ] At least **2 Arabic** phone screenshots
- [ ] **Adaptive icon** (`mipmap-anydpi-v26` with foreground/background layers) should be added
      to the app itself before launch — the current legacy mipmaps will look cropped/inconsistent
      on modern launchers.

## iOS

- **Today:** the same Umrah Connect web app is installable on iPhone as a PWA — open it in
  Safari and use **Share → Add to Home Screen**. No App Store, no Mac required.
- **Later:** a native iOS wrapper can be produced with **Capacitor**. That requires a Mac with
  Xcode to build, and an Apple Developer account ($99/year) for App Store distribution.
