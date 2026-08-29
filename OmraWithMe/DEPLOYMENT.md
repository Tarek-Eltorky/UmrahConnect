# Deploying Umrah Connect to production

A practical checklist for taking the app from this dev setup to a real, publicly reachable service. Free-tier options are called out at each step.

## 1. Required environment variables

| Variable | Value | Notes |
|---|---|---|
| `OMRA_ENV` | `production` | Enables HSTS, hides dev reset links and 422 dumps. **The app refuses to start in production without a secret key.** |
| `OMRA_SECRET_KEY` | 64+ random chars | Generate once: `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Rotating it logs everyone out. |
| `OMRA_CORS_ORIGINS` | `https://yourdomain.com` | Comma-separated. Do NOT leave the localhost defaults in production. |
| `OMRA_DB_PATH` | e.g. `/data/omrawithme.db` | Put the SQLite file on a persisted volume, not inside the app directory. |
| `OMRA_TOKEN_TTL_MINUTES` | `1440` (default) | Access-token lifetime. |
| `OMRA_REFRESH_TTL_DAYS` | `30` (default) | Refresh-token lifetime. |
| `OMRA_PUBLIC_URL` | `https://yourdomain.com` | Used for canonical URLs, sitemap, OG tags, and email links. Falls back to the request base URL if unset. |
| `OMRA_SMTP_HOST` / `OMRA_SMTP_PORT` / `OMRA_SMTP_USER` / `OMRA_SMTP_PASS` / `OMRA_SMTP_FROM` | your provider | Email delivery (verification + password reset). Free tiers: Brevo, Mailjet. When unset, emails are logged instead of sent. |
| `OMRA_REQUIRE_VERIFICATION` | `1` (prod default) | Unverified users cannot post trips, join, comment, or chat. Set `0` only for local dev. |

## 2. Run command

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

Keep `--workers 1` while the rate limiter is in-memory and the DB is SQLite. To scale beyond one worker: move rate limiting to Redis and the DB to Postgres first.

## 3. HTTPS is mandatory

The Android wrapper (v1.3+) blocks cleartext traffic in release builds, browsers require HTTPS for service workers/PWA install, and users type passwords here. Put the app behind a TLS-terminating proxy:

- **Free options**: a small VPS with Caddy (automatic Let's Encrypt), or a free-tier PaaS (Render / Railway / Fly.io) which gives you HTTPS out of the box.
- If a proxy sets `X-Forwarded-For`, the rate limiter already reads it.

## 4. Data safety

- Back up the SQLite file daily (it is a single file — `sqlite3 omrawithme.db ".backup backup.db"` is atomic and safe while the app runs).
- The DB contains national IDs and passport numbers. Restrict shell access to the server, encrypt backups.
- Migrations are ad-hoc `ALTER TABLE` in `database.py::init_db()`. Introduce Alembic before schema work gets heavier.

## 5. Before announcing the app

- [ ] Set the Android release URL: `OMRA_RELEASE_SERVER_URL=https://yourdomain.com` in `../OmraWithMe_Android/gradle.properties`, add a signing config, build `assembleRelease`.
- [ ] Serve over HTTPS and verify PWA install works on iPhone Safari (Share → Add to Home Screen) and Android Chrome.
- [ ] Run the test suite: `python -m pytest tests -q` — everything must pass.
- [ ] Grant yourself admin and use the moderation panel at `/admin`: `sqlite3 omrawithme.db "UPDATE users SET is_admin=1 WHERE email='you@example.com'"`. Review open reports there regularly.
- [ ] Configure the `OMRA_SMTP_*` variables and send yourself a test verification + password-reset email. Without SMTP, verification/reset links only appear in server logs.
- [ ] Set `OMRA_PUBLIC_URL` and verify `https://yourdomain.com/sitemap.xml`, `/robots.txt`, and a trip page's OG tags (paste a trip link into WhatsApp to check the preview).

## 6. Health & monitoring

- `GET /healthz` returns `{"status":"ok"}` and touches the DB — point uptime monitoring (e.g. free UptimeRobot) at it.
- Watch the uvicorn log for `[422]` lines (validation failures) and 429s (rate-limit hits).

## 7. What is intentionally NOT here

- No payments — the platform is free by design; never add payment collection without a full compliance review.
- No analytics/trackers — privacy policy promises none.
