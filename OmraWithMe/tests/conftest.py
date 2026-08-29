"""Pytest fixtures for the Umrah Connect API test suite.

IMPORTANT: environment variables must be set BEFORE importing main/database,
because database.py builds the engine at import time and main.py calls
init_db() at import time.
"""
import itertools
import os
import sys
import tempfile
import uuid
from datetime import date, timedelta

# --- Environment setup (must precede any app import) -----------------------
_tmpdir = tempfile.mkdtemp(prefix="omra_test_")
os.environ["OMRA_DB_PATH"] = os.path.join(_tmpdir, "omra_test.db")
os.environ["OMRA_ENV"] = "development"  # needed for dev_reset_link in password reset
os.environ.setdefault("OMRA_SECRET_KEY", "pytest-only-secret-key-0123456789abcdef")

# Make the app package importable when pytest runs from anywhere
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402  (imports database, creates engine + tables)

_seq = itertools.count(1)
_phone_seq = itertools.count(1)

DEFAULT_PASSWORD = "Passw0rd123"


def unique_phone() -> str:
    """A unique, already-normalized Egyptian mobile number (+2010XXXXXXXX).
    Phones are unique per user since v1.3 — never reuse one across users."""
    return f"+2010{next(_phone_seq):08d}"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The in-memory rate limiter is keyed by client IP ('testclient' for all
    tests) — clear it around every test or register/login limits break the suite."""
    main._rate_buckets.clear()
    yield
    main._rate_buckets.clear()


@pytest.fixture(scope="session")
def client():
    with TestClient(main.app) as c:
        yield c


def unique_email() -> str:
    return f"user{next(_seq)}.{uuid.uuid4().hex[:8]}@test.example"


def unique_token() -> str:
    """A unique keyword usable to scope listing queries to one test's data."""
    return f"kw{uuid.uuid4().hex[:10]}"


@pytest.fixture
def make_user(client):
    """Factory: register a user, return (headers, user_dict).

    The user dict is augmented with the email/password used and both tokens,
    so tests can log in again or exercise the refresh flow.
    """
    def _make(email=None, password=DEFAULT_PASSWORD, **overrides):
        if email is None:
            email = unique_email()
        payload = {
            "email": email,
            "password": password,
            "full_name": "Test User",
            "phone": unique_phone(),
            "facebook_id": "fb.testuser",
            "facebook_name": "Test User FB",
        }
        payload.update(overrides)
        r = client.post("/api/register", json=payload)
        assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
        data = r.json()
        user = data["user"]
        user["password"] = password
        user["phone"] = payload["phone"]
        user["token"] = data["token"]
        user["refresh_token"] = data["refresh_token"]
        headers = {"Authorization": f"Bearer {data['token']}"}
        return headers, user

    return _make


def trip_payload(**overrides):
    """A valid AnnouncementCreate body with sane defaults and future dates."""
    dep = (date.today() + timedelta(days=30)).isoformat()
    ret = (date.today() + timedelta(days=40)).isoformat()
    payload = {
        "title": "Umrah Trip",
        "location": "Makkah",
        "departure_date": dep,
        "return_date": ret,
        "hotel_name": "Makkah Grand Hotel",
        "hotel_stars": 4,
        "room_type": "Double",
        "people_per_room": 2,
        "total_rooms_available": 5,
        "budget_per_person": 20000,
        "description": "A blessed journey to the holy city.",
        "max_participants": 10,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def make_trip(client):
    """Factory: create an announcement for the given auth headers, return its id."""
    def _make(headers, **overrides):
        r = client.post("/api/announcements", json=trip_payload(**overrides), headers=headers)
        assert r.status_code == 200, f"create trip failed: {r.status_code} {r.text}"
        return r.json()["id"]

    return _make


@pytest.fixture
def make_join_request(client):
    """Factory: create a join request, return its id."""
    def _make(headers, announcement_id, num_people=1, message="Please let me join"):
        r = client.post(
            "/api/join-requests",
            json={"announcement_id": announcement_id, "message": message, "num_people": num_people},
            headers=headers,
        )
        assert r.status_code == 200, f"join request failed: {r.status_code} {r.text}"
        return r.json()["id"]

    return _make
