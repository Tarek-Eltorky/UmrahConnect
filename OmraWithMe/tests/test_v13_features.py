"""v1.3 features: national-ID placeholders, PUT validation, PII gating,
conversation_closed, admin moderation, phone normalization/dedupe,
email verification gate, localized notifications, ISO timestamps, SEO routes."""
import re

import main
from auth import create_email_verification_token
from conftest import unique_email, unique_phone, trip_payload, DEFAULT_PASSWORD
from database import SessionLocal, User, Message


def _db_set(user_id, **fields):
    """Set columns directly on the users row (e.g. is_admin, locale)."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        for k, v in fields.items():
            setattr(user, k, v)
        db.commit()
    finally:
        db.close()


class TestNationalIdPlaceholders:
    def test_two_na_registrations_both_succeed(self, client, make_user):
        # Regression: 'N/A' used to be stored verbatim, so the SECOND user
        # without an ID hit the uniqueness check and could not register.
        _, first = make_user(national_id="N/A")
        _, second = make_user(national_id="n/a")
        assert first["id"] != second["id"]

    def test_other_placeholders_treated_as_empty(self, client, make_user):
        for placeholder in ("na", "none", "-", ""):
            make_user(national_id=placeholder)  # each must succeed

    def test_real_duplicate_national_id_still_rejected(self, client, make_user):
        nid = "29901011234567"
        make_user(national_id=nid)
        r = client.post("/api/register", json={
            "email": unique_email(),
            "password": DEFAULT_PASSWORD,
            "full_name": "Second Holder",
            "phone": unique_phone(),
            "national_id": nid,
        })
        assert r.status_code == 400
        assert "national id" in r.json()["detail"].lower()

    def test_profile_update_placeholder_does_not_store(self, client, make_user):
        headers, _ = make_user()
        r = client.put("/api/me", json={"national_id": "N/A"}, headers=headers)
        assert r.status_code == 200
        me = client.get("/api/me", headers=headers).json()
        assert me["national_id"] == ""


class TestUpdateAnnouncementValidation:
    def test_put_rejects_empty_title(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(title=""), headers=headers)
        assert r.status_code == 422  # Field(min_length=2) — same as create

        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(title="   "), headers=headers)
        assert r.status_code == 400  # whitespace-only

    def test_put_rejects_negative_budget(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(budget_per_person=-100), headers=headers)
        assert r.status_code == 422

    def test_put_rejects_return_before_departure(self, client, make_user, make_trip):
        from datetime import date, timedelta
        headers, _ = make_user()
        trip_id = make_trip(headers)
        dep = (date.today() + timedelta(days=30)).isoformat()
        ret = (date.today() + timedelta(days=20)).isoformat()
        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(departure_date=dep, return_date=ret),
                       headers=headers)
        assert r.status_code == 400
        assert "after departure" in r.json()["detail"].lower()

    def test_put_rejects_past_departure(self, client, make_user, make_trip):
        from datetime import date, timedelta
        headers, _ = make_user()
        trip_id = make_trip(headers)
        dep = (date.today() - timedelta(days=10)).isoformat()
        ret = (date.today() + timedelta(days=10)).isoformat()
        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(departure_date=dep, return_date=ret),
                       headers=headers)
        assert r.status_code == 400
        assert "past" in r.json()["detail"].lower()


class TestRequestsPIIGating:
    CONTACT_FIELDS = ("requester_email", "requester_phone",
                      "requester_facebook", "requester_facebook_name")

    def test_pending_request_hides_contact_fields(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        req_headers, _ = make_user(national_id="29901019876543", passport_number="A1234567")
        make_join_request(req_headers, trip_id)

        items = client.get(f"/api/announcements/{trip_id}/requests",
                           headers=owner_headers).json()
        item = items[0]
        assert item["status"] == "pending"
        for field in self.CONTACT_FIELDS:
            assert field not in item, f"{field} leaked before acceptance"
        # No partial digits anywhere — only booleans about documents on file
        assert item["id_on_file"] is True
        assert item["passport_on_file"] is True
        assert "requester_national_id" not in item
        assert "requester_passport" not in item
        # The always-present contract fields
        for field in ("id", "requester_id", "requester_name", "status",
                      "message", "num_people", "response_message", "created_at"):
            assert field in item

    def test_accepted_request_shows_contact_fields(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        req_headers, requester = make_user()
        req_id = make_join_request(req_headers, trip_id)
        client.post(f"/api/join-requests/{req_id}/respond",
                    json={"request_id": req_id, "status": "accepted", "response_message": "ok"},
                    headers=owner_headers)

        items = client.get(f"/api/announcements/{trip_id}/requests",
                           headers=owner_headers).json()
        item = items[0]
        assert item["status"] == "accepted"
        assert item["requester_email"] == requester["email"]
        assert item["requester_phone"] == requester["phone"]
        assert item["requester_facebook"] == "fb.testuser"
        assert item["requester_facebook_name"] == "Test User FB"
        assert item["id_on_file"] is False  # none provided at registration

    def test_documents_on_file_false_when_absent(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        req_headers, _ = make_user()  # no national id / passport
        make_join_request(req_headers, trip_id)
        item = client.get(f"/api/announcements/{trip_id}/requests",
                          headers=owner_headers).json()[0]
        assert item["id_on_file"] is False
        assert item["passport_on_file"] is False


class TestProfileMasking:
    def test_masked_fields_show_only_last_two_chars(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, owner = make_user(national_id="29901015554442",
                                         passport_number="A7654321")
        trip_id = make_trip(owner_headers)
        req_headers, _ = make_user()
        req_id = make_join_request(req_headers, trip_id)
        client.post(f"/api/join-requests/{req_id}/respond",
                    json={"request_id": req_id, "status": "accepted", "response_message": ""},
                    headers=owner_headers)

        # Shared accepted trip -> the requester may see the owner's masked docs
        data = client.get(f"/api/users/{owner['id']}/profile", headers=req_headers).json()
        assert data["has_shared_trip"] is True
        assert data["national_id_masked"].endswith("42")
        assert data["national_id_masked"] == "•" * 12 + "42"
        assert data["passport_masked"] == "•" * 6 + "21"
        # Never the leading characters
        assert "2990" not in data["national_id_masked"]


class TestConversationClosed:
    def _chat(self, make_user, make_trip, make_join_request):
        owner_headers, owner = make_user()
        trip_id = make_trip(owner_headers)
        req_headers, requester = make_user()
        req_id = make_join_request(req_headers, trip_id)
        return owner_headers, owner, req_headers, requester, trip_id, req_id

    def test_blocked_chat_403_conversation_closed(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, owner, req_headers, requester, trip_id, req_id = self._chat(
            make_user, make_trip, make_join_request)
        # Chat open while pending
        assert client.get(f"/api/requests/{req_id}/messages",
                          headers=req_headers).status_code == 200
        # Owner blocks the requester
        client.post(f"/api/users/{requester['id']}/block", json={"reason": ""},
                    headers=owner_headers)
        for headers in (req_headers, owner_headers):
            r = client.get(f"/api/requests/{req_id}/messages", headers=headers)
            assert r.status_code == 403
            assert r.json()["detail"] == "conversation_closed"
        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "hello?"}, headers=req_headers)
        assert r.status_code == 403
        assert r.json()["detail"] == "conversation_closed"

    def test_rejected_chat_403_conversation_closed(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _, req_headers, _, trip_id, req_id = self._chat(
            make_user, make_trip, make_join_request)
        client.post(f"/api/join-requests/{req_id}/respond",
                    json={"request_id": req_id, "status": "rejected", "response_message": "no"},
                    headers=owner_headers)
        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "why?"}, headers=req_headers)
        assert r.status_code == 403
        assert r.json()["detail"] == "conversation_closed"

    def test_accepted_chat_stays_open(self, client, make_user, make_trip, make_join_request):
        owner_headers, _, req_headers, _, trip_id, req_id = self._chat(
            make_user, make_trip, make_join_request)
        client.post(f"/api/join-requests/{req_id}/respond",
                    json={"request_id": req_id, "status": "accepted", "response_message": "ok"},
                    headers=owner_headers)
        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "great news"}, headers=req_headers)
        assert r.status_code == 200

    def test_deactivated_trip_closes_chat(self, client, make_user, make_trip, make_join_request):
        owner_headers, _, req_headers, _, trip_id, req_id = self._chat(
            make_user, make_trip, make_join_request)
        client.patch(f"/api/announcements/{trip_id}/toggle", headers=owner_headers)
        r = client.get(f"/api/requests/{req_id}/messages", headers=req_headers)
        assert r.status_code == 403
        assert r.json()["detail"] == "conversation_closed"

    def test_cancel_request_deletes_messages(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _, req_headers, _, trip_id, req_id = self._chat(
            make_user, make_trip, make_join_request)
        client.post(f"/api/requests/{req_id}/messages",
                    json={"content": "soon to be orphaned"}, headers=req_headers)
        assert client.delete(f"/api/join-requests/{req_id}",
                             headers=req_headers).status_code == 200
        db = SessionLocal()
        try:
            leftover = db.query(Message).filter(Message.join_request_id == req_id).count()
        finally:
            db.close()
        assert leftover == 0


class TestAdmin:
    def _report(self, client, reporter_headers, reported_id):
        r = client.post(f"/api/users/{reported_id}/report",
                        json={"reason": "spam", "details": "posting ads"},
                        headers=reporter_headers)
        assert r.status_code == 200

    def test_admin_endpoints_403_for_non_admin(self, client, make_user):
        headers, _ = make_user()
        _, other = make_user()
        assert client.get("/api/admin/reports", headers=headers).status_code == 403
        assert client.post("/api/admin/reports/1/status", json={"status": "reviewed"},
                           headers=headers).status_code == 403
        assert client.post(f"/api/admin/users/{other['id']}/deactivate",
                           headers=headers).status_code == 403
        assert client.post(f"/api/admin/users/{other['id']}/reactivate",
                           headers=headers).status_code == 403
        assert client.post("/api/admin/announcements/1/deactivate",
                           headers=headers).status_code == 403

    def test_admin_reports_flow(self, client, make_user):
        admin_headers, admin = make_user()
        _db_set(admin["id"], is_admin=True)
        reporter_headers, reporter = make_user()
        _, reported = make_user()
        self._report(client, reporter_headers, reported["id"])

        reports = client.get("/api/admin/reports?status=open", headers=admin_headers).json()
        mine = next(r for r in reports if r["reporter_id"] == reporter["id"])
        assert mine["reported_id"] == reported["id"]
        assert mine["reporter_name"] == "Test User"
        assert mine["reported_name"] == "Test User"
        assert mine["reason"] == "spam"
        assert mine["details"] == "posting ads"
        assert mine["status"] == "open"

        r = client.post(f"/api/admin/reports/{mine['id']}/status",
                        json={"status": "reviewed"}, headers=admin_headers)
        assert r.status_code == 200
        open_now = client.get("/api/admin/reports?status=open", headers=admin_headers).json()
        assert all(x["id"] != mine["id"] for x in open_now)
        reviewed = client.get("/api/admin/reports?status=reviewed", headers=admin_headers).json()
        assert any(x["id"] == mine["id"] for x in reviewed)

    def test_admin_report_status_invalid_400(self, client, make_user):
        admin_headers, admin = make_user()
        _db_set(admin["id"], is_admin=True)
        r = client.post("/api/admin/reports/1/status", json={"status": "banana"},
                        headers=admin_headers)
        assert r.status_code == 400

    def test_admin_deactivate_and_reactivate_user(self, client, make_user):
        admin_headers, admin = make_user()
        _db_set(admin["id"], is_admin=True)
        target_headers, target = make_user()

        r = client.post(f"/api/admin/users/{target['id']}/deactivate", headers=admin_headers)
        assert r.status_code == 200
        # Deactivated user can no longer log in or use their token
        assert client.post("/api/login", json={
            "email": target["email"], "password": target["password"]
        }).status_code == 401
        assert client.get("/api/me", headers=target_headers).status_code == 401

        r = client.post(f"/api/admin/users/{target['id']}/reactivate", headers=admin_headers)
        assert r.status_code == 200
        # NOT anonymized — login works again with same credentials
        assert client.post("/api/login", json={
            "email": target["email"], "password": target["password"]
        }).status_code == 200

    def test_admin_deactivate_announcement(self, client, make_user, make_trip):
        admin_headers, admin = make_user()
        _db_set(admin["id"], is_admin=True)
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)

        r = client.post(f"/api/admin/announcements/{trip_id}/deactivate", headers=admin_headers)
        assert r.status_code == 200
        assert client.get(f"/api/announcements/{trip_id}").json()["is_active"] is False


class TestPhoneNormalization:
    def _register(self, client, phone):
        return client.post("/api/register", json={
            "email": unique_email(),
            "password": DEFAULT_PASSWORD,
            "full_name": "Phone Tester",
            "phone": phone,
        })

    def test_local_egyptian_number_normalized(self, client):
        r = self._register(client, "010 1122-3344")
        assert r.status_code == 200
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        assert client.get("/api/me", headers=headers).json()["phone"] == "+201011223344"

    def test_double_zero_prefix_normalized(self, client):
        r = self._register(client, "0020 155 667 7889")
        assert r.status_code == 200
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
        assert client.get("/api/me", headers=headers).json()["phone"] == "+201556677889"

    def test_invalid_phone_rejected(self, client):
        for bad in ("12345", "not-a-phone", "+1234567", "+2010112233445566778"):
            r = self._register(client, bad)
            assert r.status_code == 400, f"{bad!r} accepted"
            assert r.json()["detail"] == "Invalid phone number"

    def test_duplicate_phone_rejected(self, client, make_user):
        _, user = make_user()
        r = self._register(client, user["phone"])
        assert r.status_code == 409
        assert "phone" in r.json()["detail"].lower()

    def test_duplicate_phone_different_formatting_rejected(self, client, make_user):
        _, user = make_user()  # phone like +2010XXXXXXXX
        local = "0" + user["phone"][3:]  # -> 010XXXXXXXX
        r = self._register(client, local)
        assert r.status_code == 409


class TestEmailVerification:
    def test_gate_blocks_unverified_then_verify_unblocks(
        self, client, make_user, monkeypatch
    ):
        monkeypatch.setattr(main, "VERIFICATION_REQUIRED", True)
        headers, user = make_user()

        r = client.post("/api/announcements", json=trip_payload(), headers=headers)
        assert r.status_code == 403
        assert r.json()["detail"] == "verification_required"

        # Verify via the token endpoint, then the same call succeeds
        token = create_email_verification_token(user["id"])
        r = client.post("/api/verify-email", json={"token": token})
        assert r.status_code == 200
        assert client.get("/api/me", headers=headers).json()["is_verified"] is True

        r = client.post("/api/announcements", json=trip_payload(), headers=headers)
        assert r.status_code == 200

    def test_gate_blocks_join_comment_and_chat(
        self, client, make_user, make_trip, make_join_request, monkeypatch
    ):
        # Verified users set everything up first...
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        req_headers, req_user = make_user()
        req_id = make_join_request(req_headers, trip_id)

        # ...then the gate turns on and unverified users are blocked everywhere
        monkeypatch.setattr(main, "VERIFICATION_REQUIRED", True)
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "hi", "num_people": 1},
                        headers=req_headers)
        assert (r.status_code, r.json()["detail"]) == (403, "verification_required")
        r = client.post("/api/comments",
                        json={"announcement_id": trip_id, "content": "nice"},
                        headers=req_headers)
        assert (r.status_code, r.json()["detail"]) == (403, "verification_required")
        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "hello"}, headers=req_headers)
        assert (r.status_code, r.json()["detail"]) == (403, "verification_required")

    def test_verify_email_bad_token_400(self, client):
        r = client.post("/api/verify-email", json={"token": "garbage.token.here"})
        assert r.status_code == 400

    def test_wrong_token_type_rejected(self, client, make_user):
        _, user = make_user()
        # An access token must NOT verify an email (typed tokens)
        r = client.post("/api/verify-email", json={"token": user["token"]})
        assert r.status_code == 400

    def test_resend_verification(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/resend-verification", headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # SMTP unset in tests -> logged, not sent
        assert r.json().get("sent") is False

    def test_resend_rate_limited(self, client, make_user):
        headers, _ = make_user()
        for _i in range(3):
            assert client.post("/api/resend-verification", headers=headers).status_code == 200
        assert client.post("/api/resend-verification", headers=headers).status_code == 429

    def test_verify_email_page_renders(self, client):
        r = client.get("/verify-email")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


class TestLocalizedNotifications:
    def test_arabic_recipient_gets_arabic_notification(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        client.put("/api/me", json={"locale": "ar"}, headers=owner_headers)
        trip_id = make_trip(owner_headers, title="Ramadan Group")
        req_headers, _ = make_user()
        make_join_request(req_headers, trip_id)

        notifs = client.get("/api/notifications", headers=owner_headers).json()["items"]
        n = next(x for x in notifs if x["notif_type"] == "new_request")
        assert "الانضمام" in n["message"]
        assert "Ramadan Group" in n["message"]
        assert n["link"] == f"/dashboard?ann={trip_id}"

    def test_english_recipient_gets_english_notification(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers, title="Shawwal Trip")
        req_headers, _ = make_user()
        make_join_request(req_headers, trip_id)
        notifs = client.get("/api/notifications", headers=owner_headers).json()["items"]
        n = next(x for x in notifs if x["notif_type"] == "new_request")
        assert "requested to join 'Shawwal Trip'" in n["message"]


class TestContentRateLimits:
    def test_announcement_creation_limited_to_5_per_hour(self, client, make_user):
        headers, _ = make_user()
        for _i in range(5):
            assert client.post("/api/announcements", json=trip_payload(),
                               headers=headers).status_code == 200
        r = client.post("/api/announcements", json=trip_payload(), headers=headers)
        assert r.status_code == 429


class TestIsoTimestampsAndSeo:
    ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_created_at_is_iso_utc(self, client, make_user, make_trip):
        from conftest import unique_token
        headers, _ = make_user()
        kw = unique_token()
        trip_id = make_trip(headers, title=f"ISO check {kw}")
        item = client.get(f"/api/announcements?keyword={kw}").json()["items"][0]
        assert self.ISO_RE.match(item["created_at"])
        detail = client.get(f"/api/announcements/{trip_id}").json()
        assert self.ISO_RE.match(detail["created_at"])

    def test_robots_txt(self, client):
        r = client.get("/robots.txt")
        assert r.status_code == 200
        assert "Sitemap:" in r.text
        assert "/sitemap.xml" in r.text

    def test_sitemap_lists_active_trip(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.get("/sitemap.xml")
        assert r.status_code == 200
        assert "application/xml" in r.headers["content-type"]
        assert f"/announcement/{trip_id}</loc>" in r.text
        assert "/safety</loc>" in r.text

    def test_announcement_page_404_html_for_browsers(self, client):
        r = client.get("/announcement/99999999", headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]
        assert "الصفحة غير موجودة" in r.text

    def test_api_404_stays_json(self, client):
        r = client.get("/api/announcements/99999999", headers={"Accept": "text/html"})
        assert r.status_code == 404
        assert r.json()["detail"] == "Announcement not found"

    def test_announcement_page_renders_for_real_trip(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.get(f"/announcement/{trip_id}")
        assert r.status_code == 200

    def test_config_version_bumped(self, client):
        assert client.get("/api/config").json()["version"] == "1.3.1"


class TestAnonymousPrivacyGating:
    """v1.3.1 — anonymous visitors see trip facts only, never user identities."""

    def test_listing_hides_creator_from_anonymous(self, client, make_user, make_trip):
        headers, _ = make_user()
        make_trip(headers)
        items = client.get("/api/announcements").json()["items"]
        assert items
        assert all(a["creator_id"] is None and a["creator_name"] == "" for a in items)

    def test_listing_shows_creator_to_logged_in(self, client, make_user, make_trip):
        headers, owner = make_user()
        trip_id = make_trip(headers)
        items = client.get("/api/announcements", headers=headers).json()["items"]
        mine = next(a for a in items if a["id"] == trip_id)
        assert mine["creator_id"] == owner["id"]
        assert mine["creator_name"] == owner["full_name"]

    def test_detail_hides_creator_and_comments_from_anonymous(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        client.post("/api/comments",
                    json={"announcement_id": trip_id, "content": "First!"},
                    headers=headers)
        d = client.get(f"/api/announcements/{trip_id}").json()
        assert d["creator_id"] is None
        assert d["creator_name"] == ""
        assert d["creator_phone"] == "" and d["creator_facebook"] == ""
        assert d["comments"] == []
        assert d["comments_count"] == 1
        # Trip facts stay public for sharing/SEO
        assert d["title"] and d["departure_date"] and d["budget_per_person"]

    def test_detail_shows_comments_to_logged_in(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        client.post("/api/comments",
                    json={"announcement_id": trip_id, "content": "Visible"},
                    headers=headers)
        viewer_headers, _ = make_user()
        d = client.get(f"/api/announcements/{trip_id}", headers=viewer_headers).json()
        assert d["comments_count"] == 1
        assert d["comments"][0]["content"] == "Visible"
        assert d["contact_visible"] is True  # verification not enforced in tests

    def test_detail_contact_needs_verified_email_when_enforced(
        self, client, make_user, make_trip, monkeypatch
    ):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        viewer_headers, _ = make_user()
        monkeypatch.setattr(main, "VERIFICATION_REQUIRED", True)
        d = client.get(f"/api/announcements/{trip_id}", headers=viewer_headers).json()
        # Identity is fine for a logged-in user, but contact waits for verification
        assert d["creator_name"] != ""
        assert d["creator_phone"] == "" and d["creator_facebook"] == ""
        assert d["contact_visible"] is False

    def test_user_profile_requires_login(self, client, make_user):
        _, user = make_user()
        r = client.get(f"/api/users/{user['id']}/profile")
        assert r.status_code == 401
