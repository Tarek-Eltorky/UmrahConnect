# AGENTS.md — Umrah Connect / OmraWithMe

> This file is the canonical context for any AI assistant (Claude Code, Copilot, Cursor, etc.) working in this repo. Read this **first** before touching anything. Conventions and pinned-version pitfalls below are real and were paid for in pain.

## 1. What this is

**Umrah Connect** is a FastAPI web app + PWA + Android WebView wrapper for connecting Egyptian Umrah pilgrims who want to share trips and accommodations. Users post trips, others request to join, they chat, accept/reject, and exchange contact info once paired.

- **Web app** (primary): FastAPI + Jinja2 + vanilla JS + SQLite, served at `http://127.0.0.1:8000`
- **Android wrapper**: WebView app at `../OmraWithMe_Android/` (separate folder) pointing at the web app
- **Languages**: English (default) + Arabic with full RTL support
- **Audience**: Arabic-speaking Egyptians (currency = EGP)

## 2. Hard-won pinned versions — DO NOT change without checking

| Package | Version | Why |
|---|---|---|
| `fastapi` | 0.135.2 | OK |
| `starlette` | 1.0.0 | **Breaking API change** for `TemplateResponse` — see §6 |
| `jinja2` | **3.1.4** | 3.1.6 raises `TypeError: unhashable type: 'dict'` with Starlette 1.0.0 template cache. Pinned. |
| `passlib` | latest with `pbkdf2_sha256` | `bcrypt` 5.0 is **incompatible** with passlib's bcrypt backend. Use `pbkdf2_sha256` only. |
| `python-jose` | 3.3.0 | JWT |
| `sqlalchemy` | 2.0+ | OK |

Virtual env: `C:\TArek_Backup\Documents\SelfLearning\MyProject\.venv\`

## 3. Repository layout

```
OmraWithMe/                     ← THIS folder, the FastAPI app
  main.py                       ← Routes + Pydantic models + business logic
  auth.py                       ← JWT helpers, password hashing, dev secret cache
  database.py                   ← SQLAlchemy models, migrations, FK enforcement
  omrawithme.db                 ← SQLite DB (gitignored in production)
  .dev_secret                   ← Auto-generated stable dev JWT secret (gitignored)
  requirements.txt
  AGENTS.md                     ← This file
  static/
    css/style.css              (versioned: cache-busted via ?v=NN)
    js/app.js                  (versioned: cache-busted via ?v=NN)
    js/i18n.js                 (versioned: cache-busted via ?v=NN)
    sw.js                      (service worker — bump CACHE_NAME on changes)
    manifest.json              (PWA manifest with shortcuts)
    images/
  templates/                    (11 Jinja2 templates)
    index.html, login.html, register.html, dashboard.html, profile.html,
    my_requests.html, user_profile.html, reset_password.html,
    create_announcement.html, edit_announcement.html, announcement_detail.html
../OmraWithMe_Android/          ← Sibling folder, Android Studio project
  app/build.gradle              (versionCode 5 / versionName 1.4, targetSdk 35, configurable SERVER_URL via gradle properties)
  app/src/main/AndroidManifest.xml
  app/src/main/java/com/umrahconnect/app/{Main,Splash}Activity.java
  app/src/main/res/values/themes.xml (uses androidx.core:core-splashscreen)
```

## 4. Running the server

**Always use `--app-dir`** because the project root is one level up:

```powershell
& "C:\TArek_Backup\Documents\SelfLearning\MyProject\.venv\Scripts\python.exe" `
    -m uvicorn main:app `
    --app-dir "C:\TArek_Backup\Documents\SelfLearning\MyProject\OmraWithMe" `
    --host 127.0.0.1 --port 8000 --reload
```

The Android emulator hits `http://10.0.2.2:8000` which resolves to host's `127.0.0.1`.

## 5. Test credentials

| Email | Password | Role |
|---|---|---|
| `ahmed@test.com` | `Test1234` | Trip creator |
| `sara@test.com` | `Test4567` | Joiner |

Password minimum: 8 chars, must contain letters + numbers (`auth.py:validate_password_strength`).

## 6. Critical conventions

### TemplateResponse — new Starlette 1.0.0 API
```python
# WRONG (old, raises 500):
templates.TemplateResponse("page.html", {"request": request, "x": 1})
# RIGHT:
templates.TemplateResponse(request, "page.html", {"x": 1})
```

### JWT secret in dev
`auth.py` reads `OMRA_SECRET_KEY` from env. If unset (dev), it generates a random key and **caches it to `.dev_secret`** so server reloads don't invalidate tokens. **Never** make this key ephemeral again — it causes "Invalid authentication credentials" loops.

In production: must set `OMRA_ENV=production` and `OMRA_SECRET_KEY=<random>`, or the app refuses to start.

### Cache busting
Static asset URLs in templates use a numeric `?v=N` suffix (current: `style.css?v=16`, `app.js?v=20`, `i18n.js?v=10`; service worker `umrah-connect-v12`). When you change a static asset, **bump the number in every template**:

```powershell
$utf8 = [System.Text.UTF8Encoding]::new($false)
Get-Item .\templates\*.html | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName, $utf8)
    $t = $t -replace 'app\.js\?v=17', 'app.js?v=18'
    [IO.File]::WriteAllText($_.FullName, $t, $utf8)
}
```

Also bump `static/sw.js`'s `CACHE_NAME = 'umrah-connect-vN'` so the service worker drops stale cache.

### File encoding — Windows pitfall
**Never use `Get-Content -Raw` + `Set-Content`** on these template files. PowerShell 5.1's default encoding is Windows-1252, which mojibake's the UTF-8 emoji (🕋, 🕌, 👋, etc.) and Arabic text. Always use:

```powershell
$utf8 = [System.Text.UTF8Encoding]::new($false)  # no BOM
$text = [IO.File]::ReadAllText($path, $utf8)
[IO.File]::WriteAllText($path, $text, $utf8)
```

### i18n architecture
- `static/js/i18n.js` exports `window.I18N` with EN + AR dictionaries
- `data-i18n="key"` on any element → text content replaced on load + on locale change
- `data-i18n-placeholder="key"` for input placeholders
- `data-i18n-title="key"`, `data-i18n-aria-label="key"` for attributes
- `<meta name="i18n-title" content="key">` in `<head>` → swaps `document.title`
- Dynamic JS content (cards, modals): wrap in `const t = (k) => (window.I18N ? I18N.t(k) : k);` and call `t('key')` in template literals
- Pages should listen to `document.addEventListener('localechange', () => reRenderFn())` to re-render dynamic content on toggle
- Lang toggle is a navbar button rendered by `app.js` `updateNavbar()`; persists choice to `localStorage` and (for logged-in users) to `/api/me` server-side
- RTL CSS: `html[dir="rtl"] { ... }` block at end of `style.css` (no auto dark mode — `prefers-color-scheme` block was removed because it clashed with the design)

### Frontend error handling
`API.request()` in `app.js`:
- On 401: clears token, redirects to `/login?session_expired=1&next=<path>`
- On 422: flattens FastAPI's array-of-errors `detail` into a readable `field: msg • field: msg` string (otherwise it renders `[object Object]`)

### Backend logging in dev
`main.py` has a `RequestValidationError` handler that prints 422 details to the uvicorn terminal when `OMRA_ENV != production`. Use this when debugging "why did my form submit fail".

### Rate limiting
In-memory sliding window in `main.py`. Limits:
- Login: 10/5min/IP, 8/5min/email
- Register: 5/10min/IP
- Password reset request: 3/10min/IP and per-email
- Report user: 5/hour/user

Resets on server restart (since it's in-memory). For production: replace with Redis.

### Database
SQLite at `OmraWithMe/omrawithme.db`. Foreign-key enforcement is **on** (PRAGMA foreign_keys=ON via SQLAlchemy event listener).
Migrations: ad-hoc `ALTER TABLE` in `database.py::init_db()` (silent fail = column exists). Replace with Alembic before scaling.

## 7. Endpoints map

| Verb | Path | Auth | Notes |
|---|---|---|---|
| GET | `/` | – | Browse trips (paginated, filterable) |
| GET | `/login`, `/register`, `/reset-password` | – | Auth pages |
| GET | `/dashboard` | required (JS gate) | Trip owner dashboard |
| GET | `/create-announcement`, `/edit-announcement/{id}` | required | Create/edit forms |
| GET | `/announcement/{id}` | – | Public trip detail |
| GET | `/my-requests`, `/profile` | required | User pages |
| GET | `/user/{id}` | – | Public profile (contact gated by accepted shared trip) |
| POST | `/api/register`, `/api/login`, `/api/password-reset/request`, `/api/password-reset/confirm` | – | Auth |
| GET/PUT | `/api/me` | required | Self info incl. `locale` |
| POST | `/api/me/delete` | required | Soft-delete + PII anonymize |
| GET/POST/PUT/PATCH | `/api/announcements[/...]` | varies | Trips CRUD |
| POST/GET | `/api/join-requests` | required | Send/list requests |
| POST/DELETE | `/api/join-requests/{id}/respond`, `/api/join-requests/{id}` | required | Accept/reject/cancel |
| GET/POST | `/api/requests/{id}/messages` | required | Chat (polling 5s currently) |
| GET/POST | `/api/notifications[/...]` | required | In-app notifications (polling 15s) |
| POST/DELETE | `/api/users/{id}/block`, `/api/me/blocked` | required | Block list |
| POST | `/api/users/{id}/report` | required | Report user |
| GET | `/api/users/{id}/profile` | optional | Public profile JSON |
| GET | `/api/config` | – | Feature flags + supported locales |
| POST | `/api/refresh` | – | Exchange refresh token for new access+refresh pair (rotating) |
| DELETE | `/api/announcements/{id}` | owner | Soft-delete trip; rejects pending requests + notifies members |
| PUT/DELETE | `/api/comments/{id}` | author (delete: author or trip owner) | Edit/delete comment |
| POST/DELETE | `/api/announcements/{id}/favorite` | required | Save/unsave a trip |
| GET | `/api/me/favorites` | required | Saved trip ids |
| GET | `/terms`, `/privacy`, `/safety` | – | Static bilingual info pages |
| GET | `/healthz` | – | Liveness + DB probe |

## 8. State at handoff

✅ **Done**:
- Auth (register/login/password-reset/account-delete)
- JWT with stable dev key (no more 401 loops on edit)
- 401 → auto-logout → login page → return-to-page after re-login
- 422 → readable field-level errors
- Trips CRUD (Makkah / Madinah / both — all three work)
- Join requests + accept/reject + chat (polling)
- Public user profiles + shared-trip gating
- Block / report user
- Notifications (in-app, polling 15s) — fires on new join request, accept/reject, **and new chat message** (`new_message` type; consecutive unread messages per chat are collapsed into one notification; deep-links to `/dashboard?chat=&ann=` for the trip owner or `/my-requests?chat=` for the joiner, which auto-open the chat modal)
- Rate limiting on auth endpoints
- DB indexes on hot columns, FK enforcement
- N+1 fix on `/api/my-announcements`
- Pydantic validators with max_length, ge/le constraints on all models
- PWA: manifest with 3 shortcuts, service worker v6 with controlled update banner
- i18n: EN + AR with RTL, full coverage of static UI + most dynamic JS content (≈350 translation keys in `i18n.js`)
- Android: configurable server URL, modern SplashScreen API, deep-linking via `umrahconnect://`
  - **v1.2 (versionCode 3)**: thin WebView wrapper, so the web chat-message notifications + full i18n flow through automatically. Native-side changes: `buildInitialPath` now preserves the **query string + fragment** (so `umrahconnect://my-requests?chat=5` / `dashboard?chat=5&ann=3` deep-link straight into the chat) and correctly combines host+path (previously `umrahconnect://announcement/42` dropped the `announcement` segment → `/42`); offline fallback page is now bilingual (EN/AR + RTL) via `loadDataWithBaseURL`; UA bumped to `UmrahConnectApp/1.2`. **Not** included: FCM background push (still pending — in-app polling notifications only work while the app is foregrounded, same as web).

✅ **Production-hardening pass (Aug 29, 2026)**:
- **Stored XSS fixed everywhere**: `UI.esc()` added to app.js; ALL user-generated strings interpolated into innerHTML template literals are escaped across every template. Any NEW interpolation of server data MUST go through `UI.esc(...)` (attribute values included).
- Security headers middleware in main.py (CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy; HSTS in production).
- Token refresh endpoint `/api/refresh` (rotating); `get_current_user` now enforces `type=="access"` and rejects deleted users' tokens.
- Anonymous-scraper fix: trip detail no longer returns creator phone/facebook without a login.
- EmailStr validation on register (email-validator installed); case-insensitive duplicate-email check fixed.
- Trip soft-delete (owner) with participant notification; deleted trips 404 on detail and vanish from lists.
- Listing hides past-departure trips by default (`include_past=true` to override); joining a departed trip is rejected.
- Comment edit (author) / delete (author or trip owner) + UI on the detail page.
- Favorites/saved trips: model + endpoints + heart buttons on browse cards + "saved only" filter chip.
- Legal/safety pages: `/terms`, `/privacy`, `/safety` — fully bilingual via `.lang-en`/`.lang-ar` blocks toggled by `html[lang]` CSS (long-form content pages do NOT use data-i18n keys). Register page links to terms; JS-rendered footer (app.js `_renderFooter`) links all three on every page.
- `OMRA_DB_PATH` env var to relocate the SQLite file (used by the test suite).
- pytest suite in `tests/` (run: `.venv python -m pytest tests -q` from OmraWithMe/). The in-memory rate limiter is cleared per-test via fixture.
- Android wrapper v1.3 (versionCode 4): cleartext traffic OFF except emulator loopback, mixed content NEVER_ALLOW in release, CAMERA permission removed, dead resources removed, README.md added. Release build still needs `OMRA_RELEASE_SERVER_URL` + signing config.
- **iOS**: the PWA is installable on iPhone via Safari → Share → Add to Home Screen (all templates carry apple-* meta + manifest). A native iOS wrapper is possible later via Capacitor (requires a Mac + Xcode; $99/yr Apple Developer for App Store).

✅ **Production-readiness pass v1.3.0 (Aug 29, 2026)** — fixes from the full production audit:
- **Registration 'N/A' bug fixed**: placeholder national IDs (`N/A`/`na`/`none`/`-`) are treated as empty on register AND profile update; one-time DB cleanup in `init_db()`. register.html no longer sends `'N/A'`.
- **`PUT /api/announcements/{id}` fully validated**: same Field constraints + `_parse_trip_dates()` sanity checks as create (return > departure, no past departure, checkin/checkout ordering).
- **Email verification**: `mailer.py` (SMTP via `OMRA_SMTP_HOST/PORT/USER/PASS/FROM`, logs when unset). Register sends a verify link (`/verify-email`, JWT type `verify_email`, 48h). `POST /api/verify-email`, `POST /api/resend-verification`. Gate `OMRA_REQUIRE_VERIFICATION` (default ON in production, OFF in dev/tests) → 403 `verification_required` on create-trip/join/comment/message. app.js shows a global banner for unverified users. Password reset email goes through the mailer too.
- **PII gating**: trip-requests API returns contact fields (email/phone/facebook) ONLY when `status=="accepted"`; pending shows `id_on_file`/`passport_on_file` booleans (no digits). Public-profile masks show last-2 chars only.
- **Chat closure**: messages GET/POST → 403 `conversation_closed` on block / rejected request / deactivated trip. Cancel-request deletes its messages. Frontend stops polling + shows a banner. Blocking now actually blocks messaging.
- **Admin moderation**: `is_admin` column; `/admin` page (English-only internal tool); `GET/POST /api/admin/reports*`, user deactivate/reactivate, trip deactivate. Grant admin: `sqlite3 omrawithme.db "UPDATE users SET is_admin=1 WHERE email='...'"`.
- **Rate limits (per user)**: trips 5/h, join requests 10/h, comments 20/h, chat messages 120/h.
- **Cairo timezone** (`zoneinfo` + `tzdata` dep) for all departure-date logic; all API `created_at` now ISO `YYYY-MM-DDTHH:MM:SSZ` and formatted client-side.
- **Phone**: normalized to E.164 (`01x` → `+201x`) on register/profile; invalid → 400; duplicate (non-deleted users) → 409. `UI.waLink(phone)` builds wa.me links.
- **Localized notifications**: server composes notification text in the recipient's stored locale (`NOTIF_TEXTS` in main.py); new-request links deep-link to `/dashboard?ann={id}`.
- **Refresh tokens wired in app.js**: stored as `umrah_refresh`; 401 → refresh once → retry; network/non-JSON errors → translated `conn_error` message.
- **SEO**: `/robots.txt`, `/sitemap.xml`, HTML 404 page (`templates/404.html`), `/announcement/{id}` 404s for missing trips and passes `meta_title/meta_description/canonical_url/og_image_url`; all page routes receive `public_url` (env `OMRA_PUBLIC_URL` or request base). index og:title mojibake fixed; canonical/og:url/twitter tags added.
- **A11y**: modals get role=dialog/aria-modal/focus-trap/ESC automatically via `openModal`; every modal close fires `window.__onModalClose(id)`; alerts are aria-live and scroll into view; dropdowns/hamburger have aria-expanded; all form labels associated; focus ring passes contrast.
- **Design/CSS**: duplicate conflicting definitions consolidated (hero, section-title, location-badge, container, spinner, empty-state); all audited contrast failures fixed (computed ratios); RTL `row-reverse` double-reversal hacks removed, logical properties + `.date-arrow`/`.back-link i` auto-flip; `prefers-reduced-motion`; 44px touch targets on coarse pointers; Google Fonts (Amiri + Tajawal) loaded on all app pages (CSP updated for fonts.googleapis/gstatic).
- **Forms**: double-submit guards everywhere; show-password toggles (`.pw-field`/`.pw-toggle`); confirm-new-password on profile; client constraints mirror server (rooms≤10, budget≥0, participants≤500, join party≤20); date `min` attributes with chained mins; alert scrollIntoView.
- **Account deletion UI** (Play policy): Danger Zone on /profile → `POST /api/me/delete {password}`.
- **New i18n keys** (~45) in both EN/AR — see i18n.js; locale-aware `UI.formatDate/formatCurrency/formatRelative`.
- **Tests: 128 passing** (`tests/test_v13_features.py` covers all of the above). Config version `1.3.0`.

✅ **Anonymous-privacy pass v1.3.1 (Aug 29, 2026)** — user data is never exposed to visitors who are not signed in:
- `GET /api/announcements` + `GET /api/announcements/{id}`: `creator_id` is `null` and `creator_name` is `""` for anonymous callers. Trip facts (title, dates, hotels, budget, spots, description) stay public for SEO/sharing.
- Detail endpoint: `comments` is `[]` for anonymous callers; new `comments_count` field is always present so the UI can invite sign-in. New `contact_visible` boolean: contact fields (`creator_phone`/`creator_facebook*`) now require a **verified** email when `VERIFICATION_REQUIRED` is on (blocks scrape-by-registration), otherwise any logged-in user.
- `GET /api/users/{id}/profile` now requires login (401 anonymous) — name/photo/Facebook identity are member-only. `/user/{id}` page shows a sign-in invitation instead of fetching.
- Frontend: index cards + detail header/contact show a 🔒 "Sign in to see the organizer" link when `creator_name` is empty; comments section shows "Sign in to read the {n} comment(s)"; unverified users see "Verify your email to see contact info". New i18n keys: `login_see_host`, `login_view_comments`, `login_view_profile`, `verify_see_contact`.
- **Tests: 134 passing** (`TestAnonymousPrivacyGating`, and `test_comments.py`'s `_detail_comments` now authenticates). Config version `1.3.1`.
- Android v1.4 (versionCode 5): targetSdk 35, predictive back, edge-to-edge insets, release minify+shrink, signing config scaffold from gradle.properties, release build fails without `OMRA_RELEASE_SERVER_URL`, logcat console logging DEBUG-only, allowBackup=false.

🔄 **Pending / nice-to-have**:
- Real-time chat via WebSocket (currently 5s polling; works but inefficient); chat `?since=` cursor for scale
- FCM push notifications for Android (needs Firebase project)
- Alembic migrations; Postgres + Redis before multi-worker scaling
- Post-trip ratings/reviews; profile photo upload (Android file chooser exists, no upload endpoint)
- Server-rendered trip cards on the homepage for full SEO indexability (detail-page meta is done)
- Encrypt national ID/passport at rest; data-export endpoint
- Play listing assets (512px icon, feature graphic, screenshots) + adaptive icon
- ~~Hot-reload `localechange` listener missing on a few pages~~ **DONE** — `announcement_detail`, `user_profile`, `profile`, `create_announcement`, `edit_announcement` now build their dynamic content through `const t = (k)=>I18N.t(k)` and re-render on `localechange`. Their previously hardcoded-English JS (alerts, validation errors, button states, notification history, trip-detail body, public profile) is fully keyed in `i18n.js` (EN+AR). The auth/static pages (`login`, `register`, `reset_password`) need no listener — their content is `data-i18n` markup that `apply()` re-translates on every toggle.
  - Convention reminder: any string built in JS (innerHTML/textContent/showAlert) MUST go through `t('key')`, not a literal — literals don't translate on toggle. Static markup uses `data-i18n`.

⚠️ **Known UX rough edges**:
- Notification dropdown shows "Loading..." until first poll completes (~15s after login). Not a bug, but feels slow.
- Service worker can cache stale templates on iOS Safari — user must manually clear or wait for v-bump.

## 9. Don't

- Don't reintroduce auto-dark-mode via `prefers-color-scheme` — palette wasn't designed for it
- Don't run `Get-Content -Raw` on UTF-8 files in PowerShell 5.1
- Don't change Starlette's `TemplateResponse(request, ...)` signature back
- Don't make the JWT secret ephemeral in dev (causes session loops on every code edit)
- Don't `npm install` anything — this is a vanilla-JS app, no build step
- Don't bcrypt-upgrade passlib — `pbkdf2_sha256` is intentional
- Don't push directly to main — there is no main; treat this as a learning project

## 10. Useful one-liners

```powershell
# Start server
& "C:\TArek_Backup\Documents\SelfLearning\MyProject\.venv\Scripts\python.exe" -m uvicorn main:app --app-dir "C:\TArek_Backup\Documents\SelfLearning\MyProject\OmraWithMe" --host 127.0.0.1 --port 8000 --reload

# Rotate dev JWT secret (forces everyone to re-login)
Remove-Item OmraWithMe\.dev_secret

# Inspect a 422 — server prints it to the uvicorn terminal in dev mode

# Reset DB (destructive)
Remove-Item OmraWithMe\omrawithme.db
# then restart server, tables auto-create

# Bump app.js cache busters
$utf8 = [System.Text.UTF8Encoding]::new($false)
Get-Item .\OmraWithMe\templates\*.html | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName, $utf8)
    $t = $t -replace 'app\.js\?v=17','app.js?v=18'
    [IO.File]::WriteAllText($_.FullName, $t, $utf8)
}
```

## 11. Android (sibling folder)

Build/run from `../OmraWithMe_Android/`:

```powershell
.\gradlew.bat assembleDebug
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -t -r `
    app\build\intermediates\apk\debug\app-debug.apk
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" shell am start `
    -n "com.umrahconnect.app/.SplashActivity"
```

For real device testing: set `OMRA_DEBUG_SERVER_URL=http://<your-LAN-ip>:8000` in `gradle.properties` and rebuild.

The `-t` flag is required because debug builds are marked test-only.
