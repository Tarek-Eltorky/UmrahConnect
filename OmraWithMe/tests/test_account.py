"""Password reset, account deletion, health check, and security headers."""
from conftest import unique_email


class TestPasswordReset:
    def test_request_never_leaks_email_existence(self, client, make_user):
        _, user = make_user()
        r_known = client.post("/api/password-reset/request", json={"email": user["email"]})
        r_unknown = client.post("/api/password-reset/request", json={"email": unique_email()})
        assert r_known.status_code == 200
        assert r_unknown.status_code == 200
        assert r_known.json()["ok"] is True
        assert r_unknown.json()["ok"] is True
        assert r_known.json()["message"] == r_unknown.json()["message"]

    def test_full_reset_flow(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/password-reset/request", json={"email": user["email"]})
        link = r.json()["dev_reset_link"]  # exposed only in dev mode
        token = link.split("token=", 1)[1]

        new_password = "BrandNew456"
        r = client.post("/api/password-reset/confirm",
                        json={"token": token, "new_password": new_password})
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Old password stops working, new one works
        r = client.post("/api/login", json={"email": user["email"], "password": user["password"]})
        assert r.status_code == 401
        r = client.post("/api/login", json={"email": user["email"], "password": new_password})
        assert r.status_code == 200

    def test_reset_token_single_use(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/password-reset/request", json={"email": user["email"]})
        token = r.json()["dev_reset_link"].split("token=", 1)[1]
        assert client.post("/api/password-reset/confirm",
                           json={"token": token, "new_password": "BrandNew456"}).status_code == 200
        r = client.post("/api/password-reset/confirm",
                        json={"token": token, "new_password": "Another789x"})
        assert r.status_code == 400

    def test_confirm_with_garbage_token_400(self, client):
        r = client.post("/api/password-reset/confirm",
                        json={"token": "definitely-not-a-real-token", "new_password": "BrandNew456"})
        assert r.status_code == 400

    def test_confirm_rejects_weak_password(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/password-reset/request", json={"email": user["email"]})
        token = r.json()["dev_reset_link"].split("token=", 1)[1]
        r = client.post("/api/password-reset/confirm",
                        json={"token": token, "new_password": "lettersonly"})
        assert r.status_code == 400


class TestAccountDeletion:
    def test_delete_wrong_password_400(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/me/delete", json={"password": "Wrong1234"}, headers=headers)
        assert r.status_code == 400
        # Account still alive
        assert client.get("/api/me", headers=headers).status_code == 200

    def test_delete_account_soft_deletes(self, client, make_user):
        headers, user = make_user()
        r = client.post("/api/me/delete", json={"password": user["password"]}, headers=headers)
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Login no longer possible
        r = client.post("/api/login", json={"email": user["email"], "password": user["password"]})
        assert r.status_code == 401

        # Existing access token stops working (get_current_user filters is_deleted)
        assert client.get("/api/me", headers=headers).status_code == 401

    def test_deleted_users_trips_become_inactive(self, client, make_user, make_trip):
        from conftest import unique_token
        headers, user = make_user()
        kw = unique_token()
        make_trip(headers, title=f"Ghost trip {kw}")
        client.post("/api/me/delete", json={"password": user["password"]}, headers=headers)
        r = client.get(f"/api/announcements?keyword={kw}")
        assert r.json()["total"] == 0


class TestHealthAndHeaders:
    def test_healthz(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_security_headers_present(self, client):
        r = client.get("/healthz")
        assert "Content-Security-Policy" in r.headers
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "Referrer-Policy" in r.headers

    def test_security_headers_on_api_responses(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        assert "Content-Security-Policy" in r.headers
        assert r.headers["X-Content-Type-Options"] == "nosniff"
