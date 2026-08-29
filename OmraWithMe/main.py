from fastapi import FastAPI, Depends, HTTPException, status, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func, text as sa_text
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr, field_validator
import os
import hashlib
import re
import secrets
import time
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from database import (
    get_db, init_db, User, Announcement, JoinRequest, Comment, Notification,
    Message, BlockedUser, UserReport, PasswordReset, Favorite
)
from auth import (
    get_password_hash, verify_password, create_access_token, create_refresh_token,
    create_password_reset_token, create_email_verification_token,
    get_current_user, get_current_user_required,
    validate_password_strength, decode_token
)
from mailer import send_email

app = FastAPI(title="Umrah Connect", description="Connect & Share Umrah Travel Plans")

# CORS — allow trusted origins (configurable via OMRA_CORS_ORIGINS, comma-separated)
_cors_origins = [
    o.strip() for o in os.environ.get(
        "OMRA_CORS_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,http://10.0.2.2:8000"
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Security headers on every response
_IS_PROD = os.environ.get("OMRA_ENV", "development").lower() == "production"

@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # CSP: self + the Font Awesome CDN used by templates; inline JS/CSS is part of the
    # current template architecture so 'unsafe-inline' stays until scripts are externalized.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if _IS_PROD:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Log 422 validation errors in dev so the user can see what failed
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _JR

@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    if os.environ.get("OMRA_ENV", "development").lower() != "production":
        print(f"[422] {request.method} {request.url.path} -> {exc.errors()}")
    # Return FastAPI's default shape so existing frontend parsing still works
    return _JR(status_code=422, content={"detail": exc.errors()})

# --- In-memory rate limiter (sliding window) ---
_rate_buckets: dict = defaultdict(list)
def rate_limit(key: str, max_calls: int, window_seconds: int) -> None:
    now = time.time()
    bucket = _rate_buckets[key]
    cutoff = now - window_seconds
    # drop old entries
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= max_calls:
        raise HTTPException(status_code=429, detail="Too many requests, please slow down")
    bucket.append(now)

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else request.client.host) or "unknown"

# --- Email verification gating ---
# When True, unverified users cannot create announcements, join requests,
# comments, or chat messages. Defaults on in production, off in dev/tests.
VERIFICATION_REQUIRED = os.environ.get("OMRA_REQUIRE_VERIFICATION", "1" if _IS_PROD else "0") == "1"

def _require_verified(user: User) -> None:
    if VERIFICATION_REQUIRED and not user.is_verified:
        raise HTTPException(status_code=403, detail="verification_required")

# --- Public URL (canonical links, sitemap, emails) ---
def _public_url(request: Request) -> str:
    return os.environ.get("OMRA_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")

# --- Cairo timezone: trip dates are Egyptian-local dates, not UTC ---
def _cairo_today():
    return datetime.now(ZoneInfo("Africa/Cairo")).date()

# --- National ID placeholder normalization ---
_ID_PLACEHOLDERS = {"n/a", "na", "none", "-", ""}

def _clean_national_id(value: str) -> str:
    """Users type 'N/A' etc. when they have no ID — treat those as empty so the
    uniqueness check never fires on placeholders."""
    v = (value or "").strip()
    return "" if v.lower() in _ID_PLACEHOLDERS else v

# --- Phone normalization (Egypt-first, stored as E.164-ish +XXXXXXXX) ---
_PHONE_RE = re.compile(r"^\+\d{8,15}$")

def _normalize_phone(raw: str) -> str:
    s = re.sub(r"[\s\-()]", "", raw or "")
    if s.startswith("+"):
        pass  # already international
    elif s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("01") and len(s) == 11 and s.isdigit():
        s = "+2" + s  # Egyptian mobile 01xxxxxxxxx -> +201xxxxxxxxx
    elif s.startswith("20") and s.isdigit():
        s = "+" + s
    if not _PHONE_RE.match(s):
        raise HTTPException(status_code=400, detail="Invalid phone number")
    return s

# --- Notification helper ---
# Localized notification texts — composed with the RECIPIENT's locale.
NOTIF_TEXTS = {
    "new_request": {
        "en": "{name} requested to join '{title}'",
        "ar": "طلب {name} الانضمام إلى رحلة «{title}»",
    },
    "request_accepted": {
        "en": "Your request to join '{title}' was accepted ✅",
        "ar": "تم قبول طلبك للانضمام إلى رحلة «{title}» ✅",
    },
    "request_rejected": {
        "en": "Your request to join '{title}' was rejected ❌",
        "ar": "نأسف، تم رفض طلبك للانضمام إلى رحلة «{title}» ❌",
    },
    "new_comment": {
        "en": "{name} commented on '{title}'",
        "ar": "أضاف {name} تعليقًا على رحلة «{title}»",
    },
    "new_message": {
        "en": "{name} sent you a message about '{title}'",
        "ar": "أرسل لك {name} رسالة بخصوص رحلة «{title}»",
    },
    "trip_cancelled": {
        "en": "Trip '{title}' was cancelled by the organizer",
        "ar": "تم إلغاء رحلة «{title}» من قِبَل المنظِّم",
    },
}

def _notif_message(db: Session, recipient_id: int, key: str, **fmt) -> str:
    recipient = db.query(User).filter(User.id == recipient_id).first()
    locale = recipient.locale if (recipient and recipient.locale in ("en", "ar")) else "en"
    return NOTIF_TEXTS[key][locale].format(**fmt)

def _create_notification(db: Session, user_id: int, message: str, link: str, notif_type: str):
    notif = Notification(user_id=user_id, message=message, link=link, notif_type=notif_type)
    db.add(notif)
    # commit handled by caller

# --- Blocked-user helper ---
def _is_blocked_between(db: Session, user_a: int, user_b: int) -> bool:
    return db.query(BlockedUser).filter(
        or_(
            (BlockedUser.blocker_id == user_a) & (BlockedUser.blocked_id == user_b),
            (BlockedUser.blocker_id == user_b) & (BlockedUser.blocked_id == user_a),
        )
    ).first() is not None

# Create directories if they don't exist
os.makedirs(os.path.join(BASE_DIR, "static/css"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "static/js"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "static/images"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Initialize database
init_db()

# ============== Pydantic Models ==============

class UserRegister(BaseModel):
    email: EmailStr = Field(..., max_length=120)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=120)
    national_id: str = Field("", max_length=32)
    facebook_id: str = Field("", max_length=120)
    facebook_name: str = Field("", max_length=120)
    phone: str = Field(..., min_length=5, max_length=24)
    passport_number: str = Field("", max_length=32)
    passport_expiry: str = Field("", max_length=10)  # YYYY-MM-DD

class UserLogin(BaseModel):
    email: str = Field(..., max_length=120)
    password: str = Field(..., max_length=128)

class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    location: str = Field(..., max_length=32)
    departure_date: str = Field("", max_length=10)
    return_date: str = Field("", max_length=10)
    hotel_name: str = Field(..., max_length=120)
    hotel_stars: int = Field(3, ge=1, le=5)
    hotel_name_madinah: str = Field("", max_length=120)
    hotel_stars_madinah: int = Field(3, ge=1, le=5)
    makkah_checkin:  str = Field("", max_length=10)
    makkah_checkout: str = Field("", max_length=10)
    madinah_checkin:  str = Field("", max_length=10)
    madinah_checkout: str = Field("", max_length=10)
    room_type: str = Field(..., max_length=24)
    people_per_room: int = Field(..., ge=1, le=10)
    total_rooms_available: int = Field(..., ge=1, le=100)
    budget_per_person: float = Field(..., ge=0, le=1_000_000)
    includes_transport: bool = False
    includes_meals: bool = False
    description: str = Field(..., min_length=1, max_length=4000)
    requirements: str = Field("", max_length=2000)
    max_participants: int = Field(..., ge=1, le=500)

class JoinRequestCreate(BaseModel):
    announcement_id: int = Field(..., ge=1)
    message: str = Field(..., min_length=1, max_length=2000)
    num_people: int = Field(1, ge=1, le=20)

class JoinRequestResponse(BaseModel):
    request_id: int
    status: str  # must be 'accepted' or 'rejected'
    response_message: str = Field("", max_length=2000)

class CommentCreate(BaseModel):
    announcement_id: int = Field(..., ge=1)
    content: str = Field(..., min_length=1, max_length=2000)
    is_suggestion: bool = False

class AnnouncementUpdate(BaseModel):
    # Same constraints as AnnouncementCreate — updates must not bypass validation
    title: str = Field(..., min_length=2, max_length=120)
    location: str = Field(..., max_length=32)
    departure_date: str = Field("", max_length=10)
    return_date: str = Field("", max_length=10)
    hotel_name: str = Field(..., max_length=120)
    hotel_stars: int = Field(3, ge=1, le=5)
    hotel_name_madinah: str = Field("", max_length=120)
    hotel_stars_madinah: int = Field(3, ge=1, le=5)
    makkah_checkin:  str = Field("", max_length=10)
    makkah_checkout: str = Field("", max_length=10)
    madinah_checkin:  str = Field("", max_length=10)
    madinah_checkout: str = Field("", max_length=10)
    room_type: str = Field(..., max_length=24)
    people_per_room: int = Field(..., ge=1, le=10)
    total_rooms_available: int = Field(..., ge=1, le=100)
    budget_per_person: float = Field(..., ge=0, le=1_000_000)
    includes_transport: bool = False
    includes_meals: bool = False
    description: str = Field(..., min_length=1, max_length=4000)
    requirements: str = Field("", max_length=2000)
    max_participants: int = Field(..., ge=1, le=500)

class UserProfileUpdate(BaseModel):
    full_name: str = Field("", max_length=120)
    phone: str = Field("", max_length=24)
    facebook_id: str = Field("", max_length=120)
    facebook_name: str = Field("", max_length=120)
    national_id: str = Field("", max_length=32)
    passport_number: str = Field("", max_length=32)
    passport_expiry: str = Field("", max_length=10)
    locale: str = Field("", max_length=4)            # "" | "en" | "ar"
    current_password: str = Field("", max_length=128) # required only when changing password
    new_password: str = Field("", max_length=128)     # if blank, password unchanged

class PasswordResetRequest(BaseModel):
    email: str = Field(..., max_length=120)

class PasswordResetConfirm(BaseModel):
    token: str = Field(..., max_length=512)
    new_password: str = Field(..., min_length=8, max_length=128)

class BlockUserBody(BaseModel):
    reason: str = Field("", max_length=200)

class ReportUserBody(BaseModel):
    reason: str = Field(..., max_length=80)
    details: str = Field("", max_length=2000)

class AccountDeleteBody(BaseModel):
    password: str = Field(..., max_length=128)

# ============== Page Routes ==============

def _page_ctx(request: Request, **extra) -> dict:
    """Common template context for every page route (canonical/og tags)."""
    ctx = {"public_url": _public_url(request)}
    ctx.update(extra)
    return ctx

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", _page_ctx(request))

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", _page_ctx(request))

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", _page_ctx(request))

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _page_ctx(request))

@app.get("/create-announcement", response_class=HTMLResponse)
async def create_announcement_page(request: Request):
    return templates.TemplateResponse(request, "create_announcement.html", _page_ctx(request))

@app.get("/announcement/{announcement_id}", response_class=HTMLResponse)
async def announcement_detail_page(request: Request, announcement_id: int, db: Session = Depends(get_db)):
    announcement = db.query(Announcement).filter(
        Announcement.id == announcement_id, Announcement.is_deleted == False
    ).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    public = _public_url(request)
    description_line = " ".join((announcement.description or "").split())[:160]
    return templates.TemplateResponse(request, "announcement_detail.html", _page_ctx(
        request,
        announcement_id=announcement_id,
        meta_title=f"{announcement.title} – Umrah Connect",
        meta_description=description_line,
        canonical_url=f"{public}/announcement/{announcement_id}",
        og_image_url=f"{public}/static/images/icon-512.png",
    ))

@app.get("/my-requests", response_class=HTMLResponse)
async def my_requests_page(request: Request):
    return templates.TemplateResponse(request, "my_requests.html", _page_ctx(request))

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    return templates.TemplateResponse(request, "profile.html", _page_ctx(request))

@app.get("/edit-announcement/{announcement_id}", response_class=HTMLResponse)
async def edit_announcement_page(request: Request, announcement_id: int):
    return templates.TemplateResponse(request, "edit_announcement.html", _page_ctx(request, announcement_id=announcement_id))

@app.get("/user/{user_id}", response_class=HTMLResponse)
async def user_profile_page(request: Request, user_id: int):
    return templates.TemplateResponse(request, "user_profile.html", _page_ctx(request, user_id=user_id))

@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(request: Request):
    return templates.TemplateResponse(request, "verify_email.html", _page_ctx(request))

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", _page_ctx(request))

# HTML 404 for browser navigation; API/JSON clients keep the default JSON shape
@app.exception_handler(StarletteHTTPException)
async def _html_404_handler(request: Request, exc: StarletteHTTPException):
    if (
        exc.status_code == 404
        and not request.url.path.startswith("/api")
        and "text/html" in request.headers.get("accept", "")
    ):
        return templates.TemplateResponse(request, "404.html", _page_ctx(request), status_code=404)
    return await http_exception_handler(request, exc)

# ============== API Routes ==============

def _send_verification_email(request: Request, user: User) -> bool:
    token = create_email_verification_token(user.id)
    link = f"{_public_url(request)}/verify-email?token={token}"
    html = (
        f"<p>Salam {user.full_name},</p>"
        f"<p>Welcome to Umrah Connect! Please confirm your email address by clicking the link below:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>The link is valid for 48 hours. If you did not create this account, you can ignore this email.</p>"
        f"<hr>"
        f"<p dir='rtl'>السلام عليكم {user.full_name}،</p>"
        f"<p dir='rtl'>أهلًا بك في Umrah Connect! يُرجى تأكيد بريدك الإلكتروني بالضغط على الرابط أعلاه. الرابط صالح لمدة 48 ساعة.</p>"
    )
    return send_email(user.email, "Confirm your email – Umrah Connect", html)

@app.post("/api/register")
async def register(user_data: UserRegister, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"register:{_client_ip(request)}", max_calls=5, window_seconds=600)
    validate_password_strength(user_data.password)

    # Check if email exists (emails are stored lowercased — compare lowercased)
    if db.query(User).filter(User.email == user_data.email.lower().strip()).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Placeholder values ('N/A', 'none', '-', ...) are treated as "no ID"
    national_id = _clean_national_id(user_data.national_id)

    # Check if national ID exists (only if provided and non-empty)
    if national_id and db.query(User).filter(User.national_id == national_id).first():
        raise HTTPException(status_code=400, detail="National ID already registered")

    # Normalize + dedupe phone number
    phone = _normalize_phone(user_data.phone)
    if db.query(User).filter(User.phone == phone, User.is_deleted == False).first():
        raise HTTPException(status_code=409, detail="Phone number already registered")

    # Create user (email verification link is sent below)
    user = User(
        email=user_data.email.lower().strip(),
        hashed_password=get_password_hash(user_data.password),
        full_name=user_data.full_name.strip(),
        national_id=national_id or None,
        facebook_id=user_data.facebook_id,
        facebook_name=user_data.facebook_name,
        phone=phone,
        passport_number=user_data.passport_number or None,
        passport_expiry=user_data.passport_expiry or None,
        is_verified=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Send the verification email (logged to the server console when SMTP is unset)
    _send_verification_email(request, user)

    token = create_access_token(data={"sub": str(user.id)})
    refresh = create_refresh_token(user.id)

    return {
        "message": "Registration successful",
        "token": token,
        "refresh_token": refresh,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "facebook_name": user.facebook_name
        }
    }

@app.post("/api/login")
async def login(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    rate_limit(f"login:{ip}", max_calls=10, window_seconds=300)
    rate_limit(f"login:{user_data.email.lower().strip()}", max_calls=8, window_seconds=300)

    user = db.query(User).filter(User.email == user_data.email.lower().strip()).first()

    if not user or user.is_deleted or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(data={"sub": str(user.id)})
    refresh = create_refresh_token(user.id)

    return {
        "message": "Login successful",
        "token": token,
        "refresh_token": refresh,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "facebook_name": user.facebook_name
        }
    }

class RefreshBody(BaseModel):
    refresh_token: str = Field(..., max_length=1024)

@app.post("/api/refresh")
async def refresh_access_token(body: RefreshBody, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"refresh:{_client_ip(request)}", max_calls=30, window_seconds=300)
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh" or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = db.query(User).filter(User.id == int(payload["sub"]), User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    return {
        "token": create_access_token(data={"sub": str(user.id)}),
        "refresh_token": create_refresh_token(user.id),  # rotate
    }

@app.get("/api/me")
async def get_me(current_user: User = Depends(get_current_user_required)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "facebook_id": current_user.facebook_id or "",
        "facebook_name": current_user.facebook_name or "",
        "phone": current_user.phone or "",
        "national_id": current_user.national_id or "",
        "passport_number": current_user.passport_number or "",
        "passport_expiry": current_user.passport_expiry or "",
        "locale": current_user.locale or "en",
        "is_verified": bool(current_user.is_verified),
        "is_admin": bool(current_user.is_admin),
        "member_since": current_user.created_at.strftime("%B %Y") if current_user.created_at else ""
    }


class VerifyEmailBody(BaseModel):
    token: str = Field(..., max_length=1024)

@app.post("/api/verify-email")
async def verify_email(body: VerifyEmailBody, db: Session = Depends(get_db)):
    payload = decode_token(body.token)
    if not payload or payload.get("type") != "verify_email" or not payload.get("sub"):
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    user = db.query(User).filter(User.id == int(payload["sub"]), User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    if not user.is_verified:
        user.is_verified = True
        db.commit()
    return {"ok": True, "message": "Email verified successfully"}

@app.post("/api/resend-verification")
async def resend_verification(
    request: Request,
    current_user: User = Depends(get_current_user_required),
):
    rate_limit(f"resend_verify:{current_user.id}", max_calls=3, window_seconds=600)
    if current_user.is_verified:
        return {"ok": True, "already_verified": True}
    sent = _send_verification_email(request, current_user)
    return {"ok": True, "sent": sent}

def _mask_last2(value: Optional[str]) -> str:
    """Show only the LAST 2 characters of a sensitive value, e.g. '•••••••42'."""
    if not value:
        return ""
    return "•" * max(0, len(value) - 2) + value[-2:]

@app.get("/api/users/{user_id}/profile")
async def get_user_public_profile(
    user_id: int,
    # Member profiles are for members: name, photo, and Facebook identity must
    # never be readable by anonymous visitors/scrapers.
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    data = {
        "id": user.id,
        "full_name": user.full_name,
        "profile_image": user.profile_image or "/static/images/default-avatar.png",
        "facebook_id": user.facebook_id or "",
        "facebook_name": user.facebook_name or "",
        "member_since": user.created_at.strftime("%B %Y") if user.created_at else "",
        "has_shared_trip": False,
        # trip stats (always public)
        "trips_created": db.query(Announcement).filter(Announcement.creator_id == user_id).count(),
        "trips_joined": db.query(JoinRequest).filter(
            JoinRequest.requester_id == user_id,
            JoinRequest.status == "accepted"
        ).count()
    }

    if current_user and current_user.id != user_id:
        two_years_ago = datetime.utcnow() - timedelta(days=730)
        # Viewer is requester, target is the trip creator
        shared = db.query(JoinRequest).join(
            Announcement, Announcement.id == JoinRequest.announcement_id
        ).filter(
            JoinRequest.requester_id == current_user.id,
            Announcement.creator_id == user_id,
            JoinRequest.status == "accepted",
            Announcement.departure_date >= two_years_ago
        ).first()
        if not shared:
            # Viewer is the trip creator, target is requester
            shared = db.query(JoinRequest).join(
                Announcement, Announcement.id == JoinRequest.announcement_id
            ).filter(
                JoinRequest.requester_id == user_id,
                Announcement.creator_id == current_user.id,
                JoinRequest.status == "accepted",
                Announcement.departure_date >= two_years_ago
            ).first()

        if shared:
            data["has_shared_trip"] = True
            data["phone"] = user.phone or ""
            data["national_id_masked"] = _mask_last2(user.national_id)
            data["passport_masked"] = _mask_last2(user.passport_number)
            data["passport_expiry"] = user.passport_expiry or ""

    return data

@app.put("/api/me")
async def update_profile(
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    # Password change — require current password verification
    if data.new_password:
        validate_password_strength(data.new_password)
        if not data.current_password:
            raise HTTPException(status_code=400, detail="Current password is required to set a new password")
        if not verify_password(data.current_password, current_user.hashed_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.hashed_password = get_password_hash(data.new_password)

    if data.full_name:       current_user.full_name       = data.full_name
    if data.phone:           current_user.phone           = _normalize_phone(data.phone)
    if data.facebook_id is not None:   current_user.facebook_id   = data.facebook_id
    if data.facebook_name is not None: current_user.facebook_name = data.facebook_name
    national_id = _clean_national_id(data.national_id)  # 'N/A' etc. -> empty
    if national_id:
        if db.query(User).filter(
            User.national_id == national_id, User.id != current_user.id
        ).first():
            raise HTTPException(status_code=400, detail="National ID already registered")
        current_user.national_id = national_id
    if data.passport_number: current_user.passport_number = data.passport_number
    if data.passport_expiry: current_user.passport_expiry = data.passport_expiry
    if data.locale in ("en", "ar"): current_user.locale = data.locale

    db.commit()
    db.refresh(current_user)
    return {"ok": True, "full_name": current_user.full_name, "locale": current_user.locale or "en"}

def _parse_trip_dates(data):
    """Parse + sanity-check trip dates. Shared by create and update."""
    def _parse(s): return datetime.strptime(s, "%Y-%m-%d") if s else None
    mk_in  = _parse(data.makkah_checkin)
    mk_out = _parse(data.makkah_checkout)
    md_in  = _parse(data.madinah_checkin)
    md_out = _parse(data.madinah_checkout)

    # Auto-compute overall trip dates from city-specific dates (Makkah/Madinah trips)
    if data.location == "Makkah/Madinah" and (mk_in or md_in):
        city_dates = [d for d in [mk_in, mk_out, md_in, md_out] if d]
        dep = min(city_dates)
        ret = max(city_dates)
    else:
        dep = _parse(data.departure_date)
        ret = _parse(data.return_date)

    if not dep or not ret:
        raise HTTPException(status_code=400, detail="Departure and return dates are required")
    if ret <= dep:
        raise HTTPException(status_code=400, detail="Return date must be after departure date")
    # Trip dates are Egyptian-local — compare against today's date in Cairo
    if dep.date() < _cairo_today():
        raise HTTPException(status_code=400, detail="Departure date cannot be in the past")
    if mk_in and mk_out and mk_out <= mk_in:
        raise HTTPException(status_code=400, detail="Makkah check-out must be after check-in")
    if md_in and md_out and md_out <= md_in:
        raise HTTPException(status_code=400, detail="Madinah check-out must be after check-in")

    return dep, ret, mk_in, mk_out, md_in, md_out

@app.post("/api/announcements")
async def create_announcement(
    data: AnnouncementCreate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_verified(current_user)
    rate_limit(f"ann_create:{current_user.id}", max_calls=5, window_seconds=3600)
    if not data.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    dep, ret, mk_in, mk_out, md_in, md_out = _parse_trip_dates(data)

    announcement = Announcement(
        creator_id=current_user.id,
        title=data.title,
        location=data.location,
        departure_date=dep,
        return_date=ret,
        makkah_checkin=mk_in,
        makkah_checkout=mk_out,
        madinah_checkin=md_in,
        madinah_checkout=md_out,
        hotel_name=data.hotel_name,
        hotel_stars=data.hotel_stars,
        hotel_name_madinah=data.hotel_name_madinah or None,
        hotel_stars_madinah=data.hotel_stars_madinah if data.hotel_name_madinah else None,
        room_type=data.room_type,
        people_per_room=data.people_per_room,
        total_rooms_available=data.total_rooms_available,
        budget_per_person=data.budget_per_person,
        includes_transport=data.includes_transport,
        includes_meals=data.includes_meals,
        description=data.description,
        requirements=data.requirements,
        max_participants=data.max_participants
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)
    
    return {"message": "Announcement created successfully", "id": announcement.id}

@app.put("/api/announcements/{announcement_id}")
async def update_announcement(
    announcement_id: int,
    data: AnnouncementUpdate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Trip not found")
    if announcement.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="You are not the owner of this trip")
    if announcement.spots_filled >= announcement.max_participants:
        raise HTTPException(status_code=400, detail="This trip is full and can no longer be edited")

    # Same sanity rules as create: no empty title, no past departure,
    # return after departure, check-out after check-in
    if not data.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    dep, ret, mk_in, mk_out, md_in, md_out = _parse_trip_dates(data)

    announcement.title               = data.title
    announcement.location            = data.location
    announcement.departure_date      = dep
    announcement.return_date         = ret
    announcement.makkah_checkin      = mk_in
    announcement.makkah_checkout     = mk_out
    announcement.madinah_checkin     = md_in
    announcement.madinah_checkout    = md_out
    announcement.hotel_name          = data.hotel_name
    announcement.hotel_stars         = data.hotel_stars
    announcement.hotel_name_madinah  = data.hotel_name_madinah or None
    announcement.hotel_stars_madinah = data.hotel_stars_madinah if data.hotel_name_madinah else None
    announcement.room_type           = data.room_type
    announcement.people_per_room     = data.people_per_room
    announcement.total_rooms_available = data.total_rooms_available
    announcement.budget_per_person   = data.budget_per_person
    announcement.includes_transport  = data.includes_transport
    announcement.includes_meals      = data.includes_meals
    announcement.description         = data.description
    announcement.requirements        = data.requirements
    # Only allow expanding max_participants, never shrinking below spots_filled
    if data.max_participants >= announcement.spots_filled:
        announcement.max_participants = data.max_participants

    db.commit()
    return {"message": "Trip updated successfully", "id": announcement.id}

@app.get("/api/announcements")
async def get_announcements(
    location: Optional[str] = None,
    min_budget: Optional[float] = None,
    max_budget: Optional[float] = None,
    keyword: Optional[str] = None,
    departure_from: Optional[str] = None,
    departure_to: Optional[str] = None,
    sort_by: Optional[str] = "created_at",  # created_at | departure_date | budget_per_person
    sort_order: Optional[str] = "desc",     # asc | desc
    include_past: bool = False,
    favorites_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user)
):
    page = max(1, page)
    page_size = max(1, min(50, page_size))
    query = db.query(Announcement).options(joinedload(Announcement.creator)).filter(
        Announcement.is_active == True,
        Announcement.is_deleted == False
    )

    # Hide departed trips by default — nobody can join a trip that already left.
    # Cairo-local dates with a 1-day grace: hide when departure < today(Cairo) - 1 day.
    if not include_past:
        cutoff = datetime.combine(_cairo_today() - timedelta(days=1), datetime.min.time())
        query = query.filter(Announcement.departure_date >= cutoff)

    if favorites_only:
        if not current_user:
            raise HTTPException(status_code=401, detail="Login required to view saved trips")
        fav_subq = db.query(Favorite.announcement_id).filter(Favorite.user_id == current_user.id)
        query = query.filter(Announcement.id.in_(fav_subq))

    # Exclude announcements from users the current user has blocked or who blocked them
    if current_user:
        blocked_subq = db.query(BlockedUser.blocked_id).filter(BlockedUser.blocker_id == current_user.id)
        blocker_subq = db.query(BlockedUser.blocker_id).filter(BlockedUser.blocked_id == current_user.id)
        query = query.filter(
            ~Announcement.creator_id.in_(blocked_subq),
            ~Announcement.creator_id.in_(blocker_subq),
        )
    
    if location and location != "all":
        query = query.filter(Announcement.location == location)
    if min_budget:
        query = query.filter(Announcement.budget_per_person >= min_budget)
    if max_budget:
        query = query.filter(Announcement.budget_per_person <= max_budget)
    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            or_(Announcement.title.ilike(kw), Announcement.description.ilike(kw), Announcement.hotel_name.ilike(kw))
        )
    if departure_from:
        query = query.filter(Announcement.departure_date >= datetime.strptime(departure_from, "%Y-%m-%d"))
    if departure_to:
        query = query.filter(Announcement.departure_date <= datetime.strptime(departure_to, "%Y-%m-%d"))
    
    sort_col = getattr(Announcement, sort_by if sort_by in ("departure_date", "budget_per_person", "created_at") else "created_at")
    query = query.order_by(sort_col.asc() if sort_order == "asc" else sort_col.desc())
    
    total = query.count()
    announcements = query.offset((page - 1) * page_size).limit(page_size).all()
    
    result = []
    for a in announcements:
        duration = None
        if a.departure_date and a.return_date:
            duration = (a.return_date - a.departure_date).days
        result.append({
            "id": a.id,
            "title": a.title,
            "location": a.location,
            "departure_date": a.departure_date.strftime("%Y-%m-%d") if a.departure_date else None,
            "return_date": a.return_date.strftime("%Y-%m-%d") if a.return_date else None,
            "duration_days": duration,
            "hotel_name": a.hotel_name,
            "hotel_stars": a.hotel_stars,
            "hotel_name_madinah": a.hotel_name_madinah or "",
            "hotel_stars_madinah": a.hotel_stars_madinah or 3,
            "room_type": a.room_type,
            "people_per_room": a.people_per_room,
            "budget_per_person": a.budget_per_person,
            "includes_transport": a.includes_transport,
            "includes_meals": a.includes_meals,
            "description": a.description,
            "requirements": a.requirements,
            "max_participants": a.max_participants,
            "spots_filled": a.spots_filled,
            "spots_available": a.max_participants - a.spots_filled,
            # Creator identity requires login — anonymous visitors see trip facts only
            "creator_id": (a.creator.id if (a.creator and current_user) else None),
            "creator_name": (a.creator.full_name if (a.creator and current_user) else ""),
            # creator_facebook hidden from listing — only revealed on detail page after auth
            "created_at": a.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    
    return {"items": result, "total": total, "page": page, "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size)}

@app.get("/api/announcements/{announcement_id}")
async def get_announcement(
    announcement_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user)
):
    announcement = (
        db.query(Announcement)
        .options(joinedload(Announcement.creator))
        .filter(Announcement.id == announcement_id, Announcement.is_deleted == False)
        .first()
    )

    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    
    creator = announcement.creator
    contact_ok = current_user is not None and (current_user.is_verified or not VERIFICATION_REQUIRED)

    # Comments are user-generated content with author identities — login required.
    # Anonymous visitors get an empty list plus the count so the UI can invite sign-in.
    comments_count = db.query(Comment).filter(Comment.announcement_id == announcement_id).count()
    comments_list = []
    if current_user:
        comments = (
            db.query(Comment)
            .options(joinedload(Comment.author))
            .filter(Comment.announcement_id == announcement_id)
            .order_by(Comment.created_at.desc())
            .all()
        )
        for c in comments:
            comments_list.append({
                "id": c.id,
                "content": c.content,
                "is_suggestion": c.is_suggestion,
                "author_id": c.author_id,
                "author_name": c.author.full_name if c.author else "Unknown",
                "created_at": c.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            })
    
    duration = None
    if announcement.departure_date and announcement.return_date:
        duration = (announcement.return_date - announcement.departure_date).days

    return {
        "id": announcement.id,
        "title": announcement.title,
        "location": announcement.location,
        "departure_date": announcement.departure_date.strftime("%Y-%m-%d") if announcement.departure_date else None,
        "return_date": announcement.return_date.strftime("%Y-%m-%d") if announcement.return_date else None,
        "duration_days": duration,
        "hotel_name": announcement.hotel_name,
        "hotel_stars": announcement.hotel_stars,
        "hotel_name_madinah": announcement.hotel_name_madinah or "",
        "hotel_stars_madinah": announcement.hotel_stars_madinah or 3,
        "makkah_checkin":  announcement.makkah_checkin.strftime("%Y-%m-%d")  if announcement.makkah_checkin  else "",
        "makkah_checkout": announcement.makkah_checkout.strftime("%Y-%m-%d") if announcement.makkah_checkout else "",
        "madinah_checkin":  announcement.madinah_checkin.strftime("%Y-%m-%d")  if announcement.madinah_checkin  else "",
        "madinah_checkout": announcement.madinah_checkout.strftime("%Y-%m-%d") if announcement.madinah_checkout else "",
        "room_type": announcement.room_type,
        "people_per_room": announcement.people_per_room,
        "total_rooms_available": announcement.total_rooms_available,
        "budget_per_person": announcement.budget_per_person,
        "includes_transport": announcement.includes_transport,
        "includes_meals": announcement.includes_meals,
        "description": announcement.description,
        "requirements": announcement.requirements,
        "max_participants": announcement.max_participants,
        "spots_filled": announcement.spots_filled,
        "spots_available": announcement.max_participants - announcement.spots_filled,
        # Creator identity requires login; contact details additionally require a
        # verified email (when verification is enforced) — blocks scrape-by-registration.
        "creator_id": (creator.id if (creator and current_user) else None),
        "creator_name": (creator.full_name if (creator and current_user) else ""),
        "creator_facebook": (creator.facebook_name if (creator and contact_ok) else ""),
        "creator_facebook_id": (creator.facebook_id if (creator and contact_ok) else ""),
        "creator_phone": (creator.phone if (creator and contact_ok) else ""),
        "contact_visible": bool(creator and contact_ok),
        "is_active": announcement.is_active,
        "created_at": announcement.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "comments": comments_list,
        "comments_count": comments_count
    }

@app.post("/api/join-requests")
async def create_join_request(
    data: JoinRequestCreate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_verified(current_user)
    rate_limit(f"join_create:{current_user.id}", max_calls=10, window_seconds=3600)
    # Check if announcement exists
    announcement = db.query(Announcement).filter(Announcement.id == data.announcement_id).first()
    if not announcement or announcement.is_deleted:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if not announcement.is_active:
        raise HTTPException(status_code=400, detail="This trip is not accepting new requests")

    # Check if user is the creator
    if announcement.creator_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot request to join your own announcement")

    # Trip already departed (Cairo-local date)
    if announcement.departure_date and announcement.departure_date.date() < _cairo_today():
        raise HTTPException(status_code=400, detail="This trip has already departed")

    # Blocked relationship
    if _is_blocked_between(db, current_user.id, announcement.creator_id):
        raise HTTPException(status_code=403, detail="You cannot join this trip")

    # Check if already requested
    existing = db.query(JoinRequest).filter(
        JoinRequest.announcement_id == data.announcement_id,
        JoinRequest.requester_id == current_user.id
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="You have already requested to join this trip")

    # Check availability
    if announcement.spots_filled + data.num_people > announcement.max_participants:
        raise HTTPException(status_code=400, detail="Not enough spots available")
    
    join_request = JoinRequest(
        announcement_id=data.announcement_id,
        requester_id=current_user.id,
        message=data.message,
        num_people=data.num_people,
        status="pending"
    )
    db.add(join_request)
    db.flush()  # get join_request.id before commit

    # Notify trip creator (in their own language)
    _create_notification(
        db,
        user_id=announcement.creator_id,
        message=_notif_message(db, announcement.creator_id, "new_request",
                               name=current_user.full_name, title=announcement.title),
        link=f"/dashboard?ann={announcement.id}",
        notif_type="new_request"
    )

    db.commit()
    db.refresh(join_request)
    
    return {"message": "Join request sent successfully", "id": join_request.id}

@app.get("/api/my-announcements")
async def get_my_announcements(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    # Single query: announcements + pending request count (avoids N+1)
    rows = (
        db.query(
            Announcement,
            func.count(JoinRequest.id).filter(JoinRequest.status == "pending").label("pending_count"),
        )
        .outerjoin(JoinRequest, JoinRequest.announcement_id == Announcement.id)
        .filter(Announcement.creator_id == current_user.id, Announcement.is_deleted == False)
        .group_by(Announcement.id)
        .order_by(Announcement.created_at.desc())
        .all()
    )

    return [
        {
            "id": a.id,
            "title": a.title,
            "location": a.location,
            "departure_date": a.departure_date.strftime("%Y-%m-%d") if a.departure_date else None,
            "return_date": a.return_date.strftime("%Y-%m-%d") if a.return_date else None,
            "budget_per_person": a.budget_per_person,
            "max_participants": a.max_participants,
            "spots_filled": a.spots_filled,
            "pending_requests": pending_count or 0,
            "is_active": a.is_active
        }
        for a, pending_count in rows
    ]

@app.get("/api/announcements/{announcement_id}/requests")
async def get_announcement_requests(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    # Verify ownership
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement or announcement.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    requests = (
        db.query(JoinRequest)
        .options(joinedload(JoinRequest.requester))
        .filter(JoinRequest.announcement_id == announcement_id)
        .order_by(JoinRequest.created_at.desc())
        .all()
    )
    
    result = []
    for r in requests:
        req = r.requester
        # PII contract: contact details are disclosed ONLY after the organizer
        # accepts the request. Until then only booleans about documents on file.
        item = {
            "id": r.id,
            "requester_id": req.id if req else None,
            "requester_name": req.full_name if req else "Unknown",
            "status": r.status,
            "message": r.message,
            "num_people": r.num_people,
            "response_message": r.response_message or "",
            "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "id_on_file": bool(req and req.national_id),
            "passport_on_file": bool(req and req.passport_number),
        }
        if r.status == "accepted":
            item["requester_email"] = req.email if req else ""
            item["requester_phone"] = req.phone if req else ""
            item["requester_facebook"] = req.facebook_id if req else ""
            item["requester_facebook_name"] = req.facebook_name if req else ""
        result.append(item)

    return result

@app.post("/api/join-requests/{request_id}/respond")
async def respond_to_request(
    request_id: int,
    response: JoinRequestResponse,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    if response.status not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'accepted' or 'rejected'")

    join_request = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not join_request:
        raise HTTPException(status_code=404, detail="Request not found")

    if join_request.status != "pending":
        raise HTTPException(status_code=400, detail="Request already responded to")

    # Verify ownership
    announcement = db.query(Announcement).filter(Announcement.id == join_request.announcement_id).first()
    if not announcement or announcement.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if response.status == "accepted":
        # Capacity check (race-safe: re-read inside the same session)
        if announcement.spots_filled + join_request.num_people > announcement.max_participants:
            raise HTTPException(status_code=400, detail="Not enough spots remain to accept this request")
        announcement.spots_filled += join_request.num_people

    join_request.status = response.status
    join_request.response_message = response.response_message
    join_request.responded_at = datetime.utcnow()

    # Notify requester (in their own language)
    notif_key = "request_accepted" if response.status == "accepted" else "request_rejected"
    _create_notification(
        db,
        user_id=join_request.requester_id,
        message=_notif_message(db, join_request.requester_id, notif_key, title=announcement.title),
        link=f"/my-requests",
        notif_type=response.status
    )

    db.commit()

    return {"message": f"Request {response.status}"}


@app.delete("/api/join-requests/{request_id}")
async def cancel_join_request(
    request_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    join_request = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not join_request:
        raise HTTPException(status_code=404, detail="Request not found")
    if join_request.requester_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if join_request.status != "pending":
        raise HTTPException(status_code=400, detail="Can only cancel pending requests")

    # Delete the chat thread along with the request — no orphaned messages
    db.query(Message).filter(Message.join_request_id == request_id).delete()
    db.delete(join_request)
    db.commit()
    return {"message": "Request cancelled"}


@app.delete("/api/announcements/{announcement_id}")
async def delete_announcement(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    announcement = db.query(Announcement).filter(
        Announcement.id == announcement_id, Announcement.is_deleted == False
    ).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if announcement.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    announcement.is_deleted = True
    announcement.is_active = False

    # Tell everyone with a pending/accepted request that the trip is gone
    affected = db.query(JoinRequest).filter(
        JoinRequest.announcement_id == announcement_id,
        JoinRequest.status.in_(["pending", "accepted"])
    ).all()
    for r in affected:
        if r.status == "pending":
            r.status = "rejected"
            r.response_message = "Trip was cancelled by the organizer"
            r.responded_at = datetime.utcnow()
        _create_notification(
            db,
            user_id=r.requester_id,
            message=_notif_message(db, r.requester_id, "trip_cancelled", title=announcement.title),
            link="/my-requests",
            notif_type="rejected"
        )
    db.commit()
    return {"message": "Trip deleted"}

@app.patch("/api/announcements/{announcement_id}/toggle")
async def toggle_announcement(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if announcement.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    announcement.is_active = not announcement.is_active
    db.commit()
    return {"message": "Updated", "is_active": announcement.is_active}

@app.get("/api/my-join-requests")
async def get_my_join_requests(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    requests = db.query(JoinRequest).filter(
        JoinRequest.requester_id == current_user.id
    ).order_by(JoinRequest.created_at.desc()).all()
    
    result = []
    for r in requests:
        announcement = db.query(Announcement).options(joinedload(Announcement.creator)).filter(Announcement.id == r.announcement_id).first()
        creator = announcement.creator if announcement else None
        result.append({
            "id": r.id,
            "announcement_id": r.announcement_id,
            "announcement_title": announcement.title if announcement else "Unknown",
            "announcement_location": announcement.location if announcement else "",
            "creator_id": creator.id if creator else None,
            "creator_name": creator.full_name if creator else "",
            "creator_phone": creator.phone if creator else "",
            "creator_facebook_id": creator.facebook_id if creator else "",
            "creator_facebook_name": creator.facebook_name if creator else "",
            "message": r.message,
            "num_people": r.num_people,
            "status": r.status,
            "response_message": r.response_message,
            "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    
    return result

@app.post("/api/comments")
async def create_comment(
    data: CommentCreate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_verified(current_user)
    rate_limit(f"comment_create:{current_user.id}", max_calls=20, window_seconds=3600)

    # Check if announcement exists
    announcement = db.query(Announcement).filter(Announcement.id == data.announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")

    comment = Comment(
        announcement_id=data.announcement_id,
        author_id=current_user.id,
        content=data.content,
        is_suggestion=data.is_suggestion
    )
    db.add(comment)

    # Notify the trip creator about the new comment (not about their own)
    if announcement.creator_id and announcement.creator_id != current_user.id:
        _create_notification(
            db,
            user_id=announcement.creator_id,
            message=_notif_message(db, announcement.creator_id, "new_comment",
                                   name=current_user.full_name, title=announcement.title),
            link=f"/announcement/{announcement.id}",
            notif_type="new_comment"
        )

    db.commit()
    db.refresh(comment)

    return {"message": "Comment added successfully", "id": comment.id}


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)

@app.put("/api/comments/{comment_id}")
async def update_comment(
    comment_id: int,
    data: CommentUpdate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    comment.content = data.content
    db.commit()
    return {"message": "Comment updated"}

@app.delete("/api/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    # Author or the trip owner may remove a comment
    announcement = db.query(Announcement).filter(Announcement.id == comment.announcement_id).first()
    if current_user.id not in (comment.author_id, announcement.creator_id if announcement else None):
        raise HTTPException(status_code=403, detail="Not authorized")
    db.delete(comment)
    db.commit()
    return {"message": "Comment deleted"}


# ─── Favorites (saved trips) ───────────────────────────────────────────────

@app.post("/api/announcements/{announcement_id}/favorite")
async def add_favorite(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    announcement = db.query(Announcement).filter(
        Announcement.id == announcement_id, Announcement.is_deleted == False
    ).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    existing = db.query(Favorite).filter(
        Favorite.user_id == current_user.id,
        Favorite.announcement_id == announcement_id
    ).first()
    if not existing:
        db.add(Favorite(user_id=current_user.id, announcement_id=announcement_id))
        db.commit()
    return {"ok": True, "favorited": True}

@app.delete("/api/announcements/{announcement_id}/favorite")
async def remove_favorite(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    db.query(Favorite).filter(
        Favorite.user_id == current_user.id,
        Favorite.announcement_id == announcement_id
    ).delete()
    db.commit()
    return {"ok": True, "favorited": False}

@app.get("/api/me/favorites")
async def list_favorite_ids(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    ids = [f.announcement_id for f in db.query(Favorite).filter(Favorite.user_id == current_user.id).all()]
    return {"ids": ids}


# ─── Message (chat) endpoints ──────────────────────────────────────────────

class MessageCreate(BaseModel):
    content: str

def _check_conversation_open(db: Session, req: JoinRequest, ann: Optional[Announcement]) -> None:
    """A chat is closed when the parties have a block between them, the request
    was rejected, or the trip is deleted/deactivated. Pending + accepted stay open."""
    if (
        not ann
        or ann.is_deleted
        or not ann.is_active
        or req.status == "rejected"
        or _is_blocked_between(db, req.requester_id, ann.creator_id)
    ):
        raise HTTPException(status_code=403, detail="conversation_closed")

@app.get("/api/requests/{request_id}/messages")
async def get_messages(
    request_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    req = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    # Only the requester or the trip creator may read messages
    ann = db.query(Announcement).filter(Announcement.id == req.announcement_id).first()
    if current_user.id not in (req.requester_id, ann.creator_id if ann else None):
        raise HTTPException(status_code=403, detail="Not authorized")
    _check_conversation_open(db, req, ann)
    # Mark incoming messages as read
    db.query(Message).filter(
        Message.join_request_id == request_id,
        Message.sender_id != current_user.id,
        Message.is_read == False
    ).update({"is_read": True})
    db.commit()
    msgs = db.query(Message).filter(
        Message.join_request_id == request_id
    ).order_by(Message.created_at.asc()).all()
    return [
        {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": m.sender.full_name if m.sender else "",
            "content": m.content,
            "created_at": m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_read": m.is_read
        }
        for m in msgs
    ]

@app.post("/api/requests/{request_id}/messages")
async def send_message(
    request_id: int,
    data: MessageCreate,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_verified(current_user)
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    req = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    ann = db.query(Announcement).filter(Announcement.id == req.announcement_id).first()
    if current_user.id not in (req.requester_id, ann.creator_id if ann else None):
        raise HTTPException(status_code=403, detail="Not authorized")
    _check_conversation_open(db, req, ann)
    rate_limit(f"msg_create:{current_user.id}", max_calls=120, window_seconds=3600)
    msg = Message(join_request_id=request_id, sender_id=current_user.id, content=data.content.strip())
    db.add(msg)

    # Notify the OTHER party (the recipient) that they have a new message.
    recipient_id = ann.creator_id if current_user.id == req.requester_id else req.requester_id
    if recipient_id and not _is_blocked_between(db, current_user.id, recipient_id):
        # Recipient reads chat from the dashboard if they own the trip, else from my-requests.
        if recipient_id == ann.creator_id:
            link = f"/dashboard?chat={request_id}&ann={ann.id}"
        else:
            link = f"/my-requests?chat={request_id}"
        text = _notif_message(db, recipient_id, "new_message",
                              name=current_user.full_name, title=ann.title)
        # Collapse a burst of messages: reuse the existing unread notification for this
        # same chat instead of stacking one per message.
        existing = db.query(Notification).filter(
            Notification.user_id == recipient_id,
            Notification.notif_type == "new_message",
            Notification.link == link,
            Notification.is_read == False
        ).first()
        if existing:
            existing.message = text
            existing.created_at = datetime.utcnow()
        else:
            _create_notification(db, user_id=recipient_id, message=text, link=link, notif_type="new_message")

    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "created_at": msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}

@app.get("/api/requests/{request_id}/unread-count")
async def get_request_unread_count(
    request_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    req = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not req:
        return {"count": 0}
    count = db.query(Message).filter(
        Message.join_request_id == request_id,
        Message.sender_id != current_user.id,
        Message.is_read == False
    ).count()
    return {"count": count}

# ─── Notification endpoints ────────────────────────────────────────────────

@app.get("/api/notifications/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).count()
    return {"count": count}


@app.get("/api/notifications")
async def get_notifications(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    items = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc()).limit(50).all()
    return {
        "items": [
            {
                "id": n.id,
                "message": n.message,
                "link": n.link,
                "notif_type": n.notif_type,
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            for n in items
        ]
    }


@app.post("/api/notifications/{notif_id}/read")
async def mark_notification_read(
    notif_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.user_id == current_user.id
    ).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"ok": True}


@app.post("/api/notifications/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"ok": True}


# ─── Password reset ────────────────────────────────────────────────────────

def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()

@app.post("/api/password-reset/request")
async def request_password_reset(body: PasswordResetRequest, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"pwreset:{_client_ip(request)}", max_calls=3, window_seconds=600)
    rate_limit(f"pwreset:{body.email.lower().strip()}", max_calls=3, window_seconds=600)

    user = db.query(User).filter(User.email == body.email.lower().strip(), User.is_deleted == False).first()
    # Always return the same response shape — don't leak whether an email exists
    response = {"ok": True, "message": "If that email exists, a reset link was generated."}

    if not user:
        return response

    raw_token = secrets.token_urlsafe(32)
    pr = PasswordReset(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db.add(pr)
    db.commit()

    # Email the reset link through the mailer. When SMTP is unset the mailer
    # logs the message instead — keep the dev-log + dev_reset_link behavior.
    reset_link = f"/reset-password?token={raw_token}"
    full_link = f"{_public_url(request)}{reset_link}"
    html = (
        f"<p>Salam {user.full_name},</p>"
        f"<p>We received a request to reset your Umrah Connect password. "
        f"Click the link below to choose a new one (valid for 1 hour):</p>"
        f'<p><a href="{full_link}">{full_link}</a></p>'
        f"<p>If you did not request this, you can safely ignore this email.</p>"
    )
    sent = send_email(user.email, "Reset your password – Umrah Connect", html)
    if not sent:
        print(f"[password-reset] user={user.email} link={reset_link}")
    if os.environ.get("OMRA_ENV", "development").lower() != "production":
        response["dev_reset_link"] = reset_link
    return response

@app.post("/api/password-reset/confirm")
async def confirm_password_reset(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    validate_password_strength(body.new_password)
    token_hash = _hash_token(body.token)
    pr = db.query(PasswordReset).filter(
        PasswordReset.token_hash == token_hash,
        PasswordReset.used == False,
        PasswordReset.expires_at > datetime.utcnow()
    ).first()
    if not pr:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    user = db.query(User).filter(User.id == pr.user_id, User.is_deleted == False).first()
    if not user:
        raise HTTPException(status_code=400, detail="User no longer exists")
    user.hashed_password = get_password_hash(body.new_password)
    pr.used = True
    db.commit()
    return {"ok": True, "message": "Password reset successfully"}


# ─── Account deletion (soft delete) ────────────────────────────────────────

@app.post("/api/me/delete")
async def delete_my_account(
    body: AccountDeleteBody,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Password is incorrect")
    # Soft delete: deactivate trips, anonymize PII
    current_user.is_deleted = True
    current_user.email = f"deleted-{current_user.id}@removed.local"
    current_user.full_name = "Deleted user"
    current_user.phone = ""
    current_user.national_id = None
    current_user.passport_number = None
    current_user.passport_expiry = None
    current_user.facebook_id = ""
    current_user.facebook_name = ""
    current_user.hashed_password = get_password_hash(secrets.token_urlsafe(32))
    # Disable their trips
    db.query(Announcement).filter(Announcement.creator_id == current_user.id).update(
        {"is_active": False}
    )
    db.commit()
    return {"ok": True}


# ─── Block / Report ────────────────────────────────────────────────────────

@app.post("/api/users/{user_id}/block")
async def block_user(
    user_id: int,
    body: BlockUserBody,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    existing = db.query(BlockedUser).filter(
        BlockedUser.blocker_id == current_user.id,
        BlockedUser.blocked_id == user_id
    ).first()
    if existing:
        return {"ok": True, "already_blocked": True}
    db.add(BlockedUser(blocker_id=current_user.id, blocked_id=user_id, reason=body.reason))
    db.commit()
    return {"ok": True}

@app.delete("/api/users/{user_id}/block")
async def unblock_user(
    user_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    db.query(BlockedUser).filter(
        BlockedUser.blocker_id == current_user.id,
        BlockedUser.blocked_id == user_id
    ).delete()
    db.commit()
    return {"ok": True}

@app.get("/api/me/blocked")
async def list_blocked_users(
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    rows = (
        db.query(BlockedUser, User)
        .join(User, User.id == BlockedUser.blocked_id)
        .filter(BlockedUser.blocker_id == current_user.id)
        .order_by(BlockedUser.created_at.desc())
        .all()
    )
    return [
        {"id": u.id, "full_name": u.full_name, "blocked_at": b.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": b.reason}
        for b, u in rows
    ]

@app.post("/api/users/{user_id}/report")
async def report_user(
    user_id: int,
    body: ReportUserBody,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot report yourself")
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User not found")
    rate_limit(f"report:{current_user.id}", max_calls=5, window_seconds=3600)
    report = UserReport(
        reporter_id=current_user.id,
        reported_id=user_id,
        reason=body.reason,
        details=body.details,
    )
    db.add(report)
    db.commit()
    return {"ok": True, "message": "Thanks — your report has been recorded."}


# ─── Admin moderation ──────────────────────────────────────────────────────
# There is no admin UI for granting admin rights — promote via sqlite directly:
#   sqlite3 omrawithme.db "UPDATE users SET is_admin=1 WHERE email='you@example.com';"
# (see also the comment in templates/admin.html)

def _require_admin(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admins only")

class ReportStatusBody(BaseModel):
    status: str = Field(..., max_length=16)  # open | reviewed | dismissed

@app.get("/api/admin/reports")
async def admin_list_reports(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    query = db.query(UserReport)
    if status in ("open", "reviewed", "dismissed"):
        query = query.filter(UserReport.status == status)
    reports = query.order_by(UserReport.created_at.desc()).limit(200).all()
    # Join reporter/reported names in one pass
    user_ids = {r.reporter_id for r in reports} | {r.reported_id for r in reports}
    names = {}
    if user_ids:
        names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    return [
        {
            "id": r.id,
            "reporter_id": r.reporter_id,
            "reporter_name": names.get(r.reporter_id, "Unknown"),
            "reported_id": r.reported_id,
            "reported_name": names.get(r.reported_id, "Unknown"),
            "reason": r.reason,
            "details": r.details or "",
            "status": r.status,
            "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        for r in reports
    ]

@app.post("/api/admin/reports/{report_id}/status")
async def admin_set_report_status(
    report_id: int,
    body: ReportStatusBody,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    if body.status not in ("open", "reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="Status must be open, reviewed or dismissed")
    report = db.query(UserReport).filter(UserReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = body.status
    db.commit()
    return {"ok": True, "status": report.status}

@app.post("/api/admin/users/{user_id}/deactivate")
async def admin_deactivate_user(
    user_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # User has no is_active column — soft-delete flag doubles as deactivation.
    # Do NOT anonymize: reactivation must restore the account intact.
    user.is_deleted = True
    db.commit()
    return {"ok": True}

@app.post("/api/admin/users/{user_id}/reactivate")
async def admin_reactivate_user(
    user_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_deleted = False
    db.commit()
    return {"ok": True}

@app.post("/api/admin/announcements/{announcement_id}/deactivate")
async def admin_deactivate_announcement(
    announcement_id: int,
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db)
):
    _require_admin(current_user)
    announcement = db.query(Announcement).filter(Announcement.id == announcement_id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")
    announcement.is_active = False
    db.commit()
    return {"ok": True}


# ─── SEO: robots.txt + sitemap.xml ──────────────────────────────────────────

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {_public_url(request)}/sitemap.xml\n"
    )

@app.get("/sitemap.xml")
async def sitemap_xml(request: Request, db: Session = Depends(get_db)):
    public = _public_url(request)
    static_paths = ["", "/safety", "/terms", "/privacy", "/login", "/register"]
    entries = [f"  <url><loc>{public}{p}</loc></url>" for p in static_paths]

    today_start = datetime.combine(_cairo_today(), datetime.min.time())
    announcements = db.query(Announcement).filter(
        Announcement.is_active == True,
        Announcement.is_deleted == False,
        Announcement.departure_date >= today_start
    ).all()
    for a in announcements:
        lastmod = (a.updated_at or a.created_at)
        lastmod_str = lastmod.strftime("%Y-%m-%d") if lastmod else ""
        entry = f"  <url><loc>{public}/announcement/{a.id}</loc>"
        if lastmod_str:
            entry += f"<lastmod>{lastmod_str}</lastmod>"
        entry += "</url>"
        entries.append(entry)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries) +
        "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


# ─── Public config (for frontend feature flags / i18n) ─────────────────────

@app.get("/api/config")
async def get_config():
    return {
        "app_name": "Umrah Connect",
        "default_locale": "en",
        "supported_locales": ["en", "ar"],
        "version": "1.3.1",
    }


@app.get("/healthz")
async def healthz(db: Session = Depends(get_db)):
    # Touch the DB so load balancers detect a wedged database, not just a live process
    db.execute(sa_text("SELECT 1"))
    return {"status": "ok"}


# ─── Password reset page ───────────────────────────────────────────────────

@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", _page_ctx(request))


# ─── Static info pages (terms / privacy / safety) ──────────────────────────

@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request, "terms.html", _page_ctx(request))

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html", _page_ctx(request))

@app.get("/safety", response_class=HTMLResponse)
async def safety_page(request: Request):
    return templates.TemplateResponse(request, "safety.html", _page_ctx(request))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)