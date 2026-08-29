# Umrah Connect — رفقة عمرة

A free platform that helps Egyptian pilgrims find trusted companions, share Umrah trips, and coordinate safely — with no payments ever handled by the platform.

## Repository layout

| Folder | What it is |
|---|---|
| [`OmraWithMe/`](OmraWithMe/) | The web application: FastAPI + SQLite backend, vanilla-JS bilingual (EN/AR, full RTL) frontend, installable PWA. |
| [`OmraWithMe_Android/`](OmraWithMe_Android/) | Android app (WebView wrapper, targetSdk 35) pointing at the deployed web app. |

## Quick start (web)

```bash
cd OmraWithMe
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000. Run the test suite with `python -m pytest tests -q` (128 tests).

## Key documents

- **[OmraWithMe/DEPLOYMENT.md](OmraWithMe/DEPLOYMENT.md)** — production checklist: environment variables (secret key, SMTP, public URL), HTTPS, backups, monitoring, admin setup.
- **[OmraWithMe/AGENTS.md](OmraWithMe/AGENTS.md)** — architecture, conventions, endpoint map, and the full changelog.
- **[OmraWithMe_Android/README.md](OmraWithMe_Android/README.md)** — Android build, release signing, and the Google Play console checklist.

## Highlights

- Email verification, refresh-token auth, per-user rate limiting, security headers, escaped output everywhere.
- Contact details are shared only after a join request is accepted; blocking fully closes conversations; an admin moderation panel reviews user reports at `/admin`.
- Bilingual English/Arabic with locale-aware dates, currency, and notifications; accessible modals, labeled forms, WCAG-checked contrast.
- SEO-ready: sitemap, robots.txt, canonical/Open Graph tags, real 404s. iOS users install the PWA via Safari → Add to Home Screen.

The platform is free by design — it never collects payments.
