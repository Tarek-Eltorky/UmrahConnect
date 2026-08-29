# GO-LIVE.md — launch runbook

This is the complete, self-contained playbook for taking Umrah Connect from this repository to a live production service, written for whoever executes the launch (a person or a coding agent). Everything code-level is already done and verified — **the steps below are the only things standing between this repo and real users.**

## Current state (verified 2026-08-29)

- Web app `OmraWithMe/` at version **1.3.1**: 134/134 tests passing (`python -m pytest tests -q`).
- Android wrapper `OmraWithMe_Android/` at versionCode 5 / versionName 1.4, targetSdk 35; `assembleDebug` builds clean.
- Security posture: typed JWTs + refresh tokens, email-verification gate, per-user & per-IP rate limits, security headers/CSP, escaped output, phone normalization + dedupe.
- Privacy posture: anonymous visitors see trip facts only (no names, no comments, no profiles); contact details require a verified account; requester PII is revealed only after the organizer accepts.
- Read `OmraWithMe/AGENTS.md` before changing any code — pinned versions and conventions there are load-bearing. `OmraWithMe/DEPLOYMENT.md` is the condensed ops checklist this runbook expands on.

## Inputs the owner must provide

| Input | Used in | Notes |
|---|---|---|
| Hosting choice + account | Phase 1 | Render / Railway / Fly.io free tiers, or any VPS |
| Domain name (optional but recommended) | Phase 1, 4 | PaaS subdomain works to start |
| SMTP credentials | Phase 2 | Free tiers: Brevo, Mailjet (a Gmail app-password also works to start) |
| Owner's account email | Phase 2 | To grant the admin role |
| Keystore passwords (choose & store safely) | Phase 4 | Losing the keystore = losing the ability to update the Play app |
| Google Play developer account ($25 one-time) | Phase 4 | Only when publishing to Play |

## Phase 1 — Deploy the web app over HTTPS

HTTPS is mandatory: the Android release build blocks cleartext, service workers/PWA install require it, and users type passwords.

### Option A — free-tier PaaS (fastest)

1. Create a new web service from this repo, root directory `OmraWithMe/`, runtime Python 3.12+.
   - Build: `pip install -r requirements.txt`
   - Start: `python -m uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
2. Attach a **persistent disk/volume** (SQLite must survive restarts and deploys) and point `OMRA_DB_PATH` at it, e.g. `/data/omrawithme.db`.
3. Set the environment variables (table below).
4. Deploy, then run the Phase 3 verification.

### Option B — VPS with Caddy (more control)

```bash
# on the server
git clone <this repo> && cd UmrahConnect/OmraWithMe
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# create /etc/systemd/system/umrahconnect.service running:
#   .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
# with the env vars below in the unit's Environment= lines, then enable it.
```

Caddyfile (automatic Let's Encrypt):

```
yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy sets `X-Forwarded-For`; the app's rate limiter already reads it.

### Environment variables

| Variable | Value | Notes |
|---|---|---|
| `OMRA_ENV` | `production` | App **refuses to start** in production without a secret key |
| `OMRA_SECRET_KEY` | 64+ random chars | `python -c "import secrets; print(secrets.token_urlsafe(64))"` — generate once, store in the host's secret manager, never commit. Rotating logs everyone out |
| `OMRA_PUBLIC_URL` | `https://yourdomain.com` | Canonical URLs, sitemap, OG tags, email links |
| `OMRA_CORS_ORIGINS` | `https://yourdomain.com` | Comma-separated; do NOT leave localhost defaults |
| `OMRA_DB_PATH` | `/data/omrawithme.db` | On the persistent volume |
| `OMRA_REQUIRE_VERIFICATION` | `1` | Production default; keeps unverified accounts read-only |
| `OMRA_SMTP_HOST/PORT/USER/PASS/FROM` | provider values | All five, or emails are only logged (Phase 2) |
| `OMRA_TOKEN_TTL_MINUTES` / `OMRA_REFRESH_TTL_DAYS` | defaults `1440` / `30` | Only change deliberately |

Keep `--workers 1` until the DB is Postgres and rate limiting is in Redis (see Phase 5).

## Phase 2 — Post-deploy configuration

1. **SMTP**: set the five `OMRA_SMTP_*` vars, restart, register a test account, and confirm the verification email arrives (check spam). Then trigger a password reset and confirm that email too.
2. **Admin**: on the server, `sqlite3 /data/omrawithme.db "UPDATE users SET is_admin=1 WHERE email='<owner email>'"` — then open `/admin` logged in as that account and confirm the moderation panel loads.
3. **Backups**: daily cron on the server/volume: `sqlite3 /data/omrawithme.db ".backup /data/backup-$(date +%u).db"` (atomic, safe while running; keeps 7 rotating copies). The DB contains national IDs and passport numbers — restrict shell access and encrypt any off-server backup copies.
4. **Monitoring**: point a free uptime monitor (e.g. UptimeRobot) at `GET /healthz` (returns `{"status":"ok"}` and touches the DB). Watch server logs for `[422]` lines and 429 bursts in the first weeks.

## Phase 3 — Verification (acceptance criteria)

All of these must pass before announcing the app:

1. `https://yourdomain.com/healthz` → `{"status":"ok"}`; `/api/config` → `"version": "1.3.1"` (or later).
2. **Anonymous privacy**: logged-out, `GET /api/announcements` items have `creator_id: null`, `creator_name: ""`; a trip detail has empty `comments` and `creator_phone: ""`; `GET /api/users/1/profile` → 401.
3. **Full user journey**: register → verification email arrives → verify → create a trip → second account requests to join → chat → accept → contact details appear for both sides → WhatsApp link works.
4. **Verification gate**: an unverified account cannot create/join/comment/chat (403 `verification_required`).
5. **PWA**: install works on Android Chrome and on iPhone Safari (Share → Add to Home Screen); pages load offline after first visit.
6. **SEO/sharing**: `/robots.txt` and `/sitemap.xml` return 200; paste a trip link into WhatsApp and confirm the preview card (title + description) renders.
7. **Arabic**: toggle to العربية — full RTL layout, dates/currency localized, no brand text other than "Umrah Connect".
8. Run the suite once against the deployed code base: `python -m pytest tests -q` → all green.

## Phase 4 — Android release

1. In `OmraWithMe_Android/gradle.properties` (NOT committed — the file is gitignored; create locally):
   ```properties
   OMRA_RELEASE_SERVER_URL=https://yourdomain.com
   OMRA_KEYSTORE_FILE=release.keystore
   OMRA_KEYSTORE_PASSWORD=<chosen>
   OMRA_KEY_ALIAS=umrahconnect
   OMRA_KEY_PASSWORD=<chosen>
   ```
2. Generate the keystore (once, then **back it up somewhere safe forever**):
   ```bash
   keytool -genkeypair -v -keystore release.keystore -alias umrahconnect \
     -keyalg RSA -keysize 2048 -validity 10000
   ```
3. Build: `./gradlew bundleRelease` (Play) or `assembleRelease` (APK). The build **fails fast by design** if `OMRA_RELEASE_SERVER_URL` is missing.
4. Install the release build on a real device and re-run the Phase 3 journey inside the app (predictive back, edge-to-edge, offline page).
5. Play Console: follow the checklist in `OmraWithMe_Android/README.md` — store listing (EN + AR), screenshots, feature graphic, adaptive icon, privacy-policy URL (`https://yourdomain.com/privacy`), data-safety form (declare: email, name, phone, national ID/passport collected, not sold, not shared with third parties), content rating, then release to internal testing before production.
6. iOS: no native app — the documented path is the PWA via Safari; revisit a wrapper (e.g. Capacitor) only if there is real demand.

## Phase 5 — Post-launch roadmap (not blockers)

In priority order once there are real users:

1. **Postgres + Redis + Alembic** — prerequisite for >1 worker and for surviving PaaS disk quirks; rate limiter moves to Redis.
2. **PII encryption at rest** for national ID / passport columns.
3. **WebSocket chat + push notifications (FCM)** — replaces the current polling.
4. **Ratings/reviews after completed trips** and server-rendered trip cards on the homepage (SEO + first-paint).

## Hard rules (do not violate)

- **No payments, ever** — the platform is free by design; never add payment collection without a full compliance review. **No analytics/trackers** — the privacy policy promises none.
- The Egypt national-ID model is intentional; do not "internationalize" identity fields.
- The brand is **"Umrah Connect" in both languages** — do not introduce an Arabic brand name.
- Never commit: `*.db`, `.dev_secret`, keystores, `gradle.properties` with secrets, or any absolute local path.
- Follow `OmraWithMe/AGENTS.md` conventions (pinned versions, cache-busting `?v=N` bumps, UTF-8 file handling, TemplateResponse signature) for any code change.
