"""Auth: register, login, /api/me, refresh-token flow."""
from conftest import unique_email, DEFAULT_PASSWORD


class TestRegister:
    def test_register_success(self, client):
        email = unique_email()
        r = client.post("/api/register", json={
            "email": email,
            "password": DEFAULT_PASSWORD,
            "full_name": "New Pilgrim",
            "phone": "+201112223344",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["token"]
        assert data["refresh_token"]
        assert data["user"]["email"] == email.lower()
        assert data["user"]["full_name"] == "New Pilgrim"

    def test_register_duplicate_email(self, client, make_user):
        email = unique_email()
        make_user(email=email)
        r = client.post("/api/register", json={
            "email": email,
            "password": DEFAULT_PASSWORD,
            "full_name": "Someone Else",
            "phone": "+201112223344",
        })
        assert r.status_code == 400
        assert "already registered" in r.json()["detail"].lower()

    def test_register_duplicate_email_different_case(self, client, make_user):
        email = unique_email()
        make_user(email=email)
        r = client.post("/api/register", json={
            "email": email.upper(),
            "password": DEFAULT_PASSWORD,
            "full_name": "Case Variant",
            "phone": "+201112223344",
        })
        assert r.status_code == 400

    def test_register_weak_password_letters_only(self, client):
        r = client.post("/api/register", json={
            "email": unique_email(),
            "password": "onlyletters",
            "full_name": "Weak Password",
            "phone": "+201112223344",
        })
        assert r.status_code == 400
        assert "letters and numbers" in r.json()["detail"]

    def test_register_weak_password_digits_only(self, client):
        r = client.post("/api/register", json={
            "email": unique_email(),
            "password": "1234567890",
            "full_name": "Weak Password",
            "phone": "+201112223344",
        })
        assert r.status_code == 400

    def test_register_short_password_rejected(self, client):
        r = client.post("/api/register", json={
            "email": unique_email(),
            "password": "a1",
            "full_name": "Short Password",
            "phone": "+201112223344",
        })
        # Pydantic min_length=8 rejects before the strength validator runs
        assert r.status_code == 422

    def test_register_invalid_email_format(self, client):
        r = client.post("/api/register", json={
            "email": "not-an-email",
            "password": DEFAULT_PASSWORD,
            "full_name": "Bad Email",
            "phone": "+201112223344",
        })
        assert r.status_code == 422


class TestLogin:
    def test_login_success(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/login", json={"email": user["email"], "password": user["password"]})
        assert r.status_code == 200
        data = r.json()
        assert data["token"]
        assert data["refresh_token"]
        assert data["user"]["id"] == user["id"]

    def test_login_wrong_password(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/login", json={"email": user["email"], "password": "Wrong1234"})
        assert r.status_code == 401

    def test_login_unknown_email(self, client):
        r = client.post("/api/login", json={"email": unique_email(), "password": DEFAULT_PASSWORD})
        assert r.status_code == 401


class TestMe:
    def test_me_requires_auth(self, client):
        r = client.get("/api/me")
        assert r.status_code == 401

    def test_me_with_garbage_token(self, client):
        r = client.get("/api/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_me_returns_profile(self, client, make_user):
        headers, user = make_user()
        r = client.get("/api/me", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == user["id"]
        assert data["email"] == user["email"]
        assert data["phone"] == user["phone"]
        assert data["is_verified"] is False
        assert data["is_admin"] is False


class TestRefresh:
    def test_refresh_returns_new_working_tokens(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/refresh", json={"refresh_token": user["refresh_token"]})
        assert r.status_code == 200
        data = r.json()
        assert data["token"]
        assert data["refresh_token"]
        # The new access token must actually work
        me = client.get("/api/me", headers={"Authorization": f"Bearer {data['token']}"})
        assert me.status_code == 200
        assert me.json()["id"] == user["id"]

    def test_refresh_token_cannot_be_used_as_access_token(self, client, make_user):
        _, user = make_user()
        r = client.get("/api/me", headers={"Authorization": f"Bearer {user['refresh_token']}"})
        assert r.status_code == 401

    def test_access_token_cannot_be_used_as_refresh_token(self, client, make_user):
        _, user = make_user()
        r = client.post("/api/refresh", json={"refresh_token": user["token"]})
        assert r.status_code == 401

    def test_garbage_refresh_token(self, client):
        r = client.post("/api/refresh", json={"refresh_token": "garbage.token.value"})
        assert r.status_code == 401
