"""Favorites (saved trips): add, list, idempotency, remove, listing filter."""
from conftest import unique_token


class TestFavorites:
    def test_add_and_list_favorite(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        user_headers, _ = make_user()

        r = client.post(f"/api/announcements/{trip_id}/favorite", headers=user_headers)
        assert r.status_code == 200
        assert r.json()["favorited"] is True

        ids = client.get("/api/me/favorites", headers=user_headers).json()["ids"]
        assert trip_id in ids

    def test_double_favorite_is_idempotent(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        user_headers, _ = make_user()

        assert client.post(f"/api/announcements/{trip_id}/favorite", headers=user_headers).status_code == 200
        assert client.post(f"/api/announcements/{trip_id}/favorite", headers=user_headers).status_code == 200
        ids = client.get("/api/me/favorites", headers=user_headers).json()["ids"]
        assert ids.count(trip_id) == 1

    def test_remove_favorite(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        user_headers, _ = make_user()
        client.post(f"/api/announcements/{trip_id}/favorite", headers=user_headers)

        r = client.delete(f"/api/announcements/{trip_id}/favorite", headers=user_headers)
        assert r.status_code == 200
        assert r.json()["favorited"] is False
        ids = client.get("/api/me/favorites", headers=user_headers).json()["ids"]
        assert trip_id not in ids

    def test_favorite_unknown_trip_404(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/announcements/99999999/favorite", headers=headers)
        assert r.status_code == 404

    def test_favorite_requires_auth(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        r = client.post(f"/api/announcements/{trip_id}/favorite")
        assert r.status_code == 401

    def test_favorites_only_listing_filter(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        kw = unique_token()
        fav_id = make_trip(owner_headers, title=f"Saved {kw}")
        other_id = make_trip(owner_headers, title=f"Unsaved {kw}")

        user_headers, _ = make_user()
        client.post(f"/api/announcements/{fav_id}/favorite", headers=user_headers)

        r = client.get(f"/api/announcements?favorites_only=true&keyword={kw}", headers=user_headers)
        assert r.status_code == 200
        ids = {i["id"] for i in r.json()["items"]}
        assert ids == {fav_id}
        assert other_id not in ids

    def test_favorites_only_without_auth_401(self, client):
        r = client.get("/api/announcements?favorites_only=true")
        assert r.status_code == 401
