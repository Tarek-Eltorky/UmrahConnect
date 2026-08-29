"""Trips: create, validate dates, list/filter/paginate, update, toggle, delete,
and anonymous privacy of creator contact info."""
from datetime import date, timedelta

from conftest import trip_payload, unique_token


def _future(days):
    return (date.today() + timedelta(days=days)).isoformat()


class TestCreateAndDetail:
    def test_create_and_get_detail(self, client, make_user, make_trip):
        headers, user = make_user()
        trip_id = make_trip(headers, title="Ramadan Umrah Special")
        r = client.get(f"/api/announcements/{trip_id}", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Ramadan Umrah Special"
        assert data["location"] == "Makkah"
        assert data["room_type"] == "Double"
        assert data["max_participants"] == 10
        assert data["spots_filled"] == 0
        assert data["spots_available"] == 10
        assert data["creator_id"] == user["id"]
        assert data["duration_days"] == 10
        assert data["is_active"] is True

    def test_create_requires_auth(self, client):
        r = client.post("/api/announcements", json=trip_payload())
        assert r.status_code == 401

    def test_reject_return_before_departure(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/announcements",
                        json=trip_payload(departure_date=_future(30), return_date=_future(25)),
                        headers=headers)
        assert r.status_code == 400
        assert "after departure" in r.json()["detail"].lower()

    def test_reject_return_equal_departure(self, client, make_user):
        headers, _ = make_user()
        d = _future(30)
        r = client.post("/api/announcements",
                        json=trip_payload(departure_date=d, return_date=d),
                        headers=headers)
        assert r.status_code == 400

    def test_reject_past_departure(self, client, make_user):
        headers, _ = make_user()
        past = (date.today() - timedelta(days=10)).isoformat()
        ret = (date.today() + timedelta(days=5)).isoformat()
        r = client.post("/api/announcements",
                        json=trip_payload(departure_date=past, return_date=ret),
                        headers=headers)
        assert r.status_code == 400
        assert "past" in r.json()["detail"].lower()

    def test_reject_missing_dates(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/announcements",
                        json=trip_payload(departure_date="", return_date=""),
                        headers=headers)
        assert r.status_code == 400

    def test_detail_unknown_id_404(self, client):
        r = client.get("/api/announcements/99999999")
        assert r.status_code == 404


class TestListing:
    def test_keyword_filter_and_pagination(self, client, make_user, make_trip):
        headers, _ = make_user()
        kw = unique_token()
        ids = [make_trip(headers, title=f"Trip {kw} number {i}") for i in range(3)]

        r = client.get(f"/api/announcements?keyword={kw}&page=1&page_size=2")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["total_pages"] == 2
        assert len(data["items"]) == 2

        r2 = client.get(f"/api/announcements?keyword={kw}&page=2&page_size=2")
        data2 = r2.json()
        assert len(data2["items"]) == 1

        listed_ids = {i["id"] for i in data["items"]} | {i["id"] for i in data2["items"]}
        assert listed_ids == set(ids)

    def test_keyword_filter_excludes_non_matching(self, client, make_user, make_trip):
        headers, _ = make_user()
        kw = unique_token()
        match_id = make_trip(headers, title=f"Special {kw} trip")
        other_id = make_trip(headers, title="Completely different title")

        r = client.get(f"/api/announcements?keyword={kw}")
        ids = {i["id"] for i in r.json()["items"]}
        assert match_id in ids
        assert other_id not in ids

    def test_listing_hides_creator_contact(self, client, make_user, make_trip):
        headers, _ = make_user()
        kw = unique_token()
        make_trip(headers, title=f"Privacy listing {kw}")
        r = client.get(f"/api/announcements?keyword={kw}")
        item = r.json()["items"][0]
        assert "creator_phone" not in item
        assert "creator_facebook" not in item


class TestUpdate:
    def test_update_by_owner(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        payload = trip_payload(title="Updated Title", budget_per_person=25000,
                               description="Updated description")
        r = client.put(f"/api/announcements/{trip_id}", json=payload, headers=headers)
        assert r.status_code == 200
        detail = client.get(f"/api/announcements/{trip_id}").json()
        assert detail["title"] == "Updated Title"
        assert detail["budget_per_person"] == 25000
        assert detail["description"] == "Updated description"

    def test_update_by_non_owner_403(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        other_headers, _ = make_user()
        r = client.put(f"/api/announcements/{trip_id}",
                       json=trip_payload(title="Hijacked"), headers=other_headers)
        assert r.status_code == 403

    def test_toggle_active(self, client, make_user, make_trip):
        headers, _ = make_user()
        kw = unique_token()
        trip_id = make_trip(headers, title=f"Toggle {kw}")

        r = client.patch(f"/api/announcements/{trip_id}/toggle", headers=headers)
        assert r.status_code == 200
        assert r.json()["is_active"] is False

        # Inactive trips disappear from the public listing
        listed = client.get(f"/api/announcements?keyword={kw}").json()
        assert listed["total"] == 0

        r = client.patch(f"/api/announcements/{trip_id}/toggle", headers=headers)
        assert r.json()["is_active"] is True
        listed = client.get(f"/api/announcements?keyword={kw}").json()
        assert listed["total"] == 1

    def test_toggle_by_non_owner_403(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        other_headers, _ = make_user()
        r = client.patch(f"/api/announcements/{trip_id}/toggle", headers=other_headers)
        assert r.status_code == 403


class TestDelete:
    def test_delete_soft_deletes_and_rejects_pending_requests(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        kw = unique_token()
        trip_id = make_trip(owner_headers, title=f"Doomed {kw}")
        requester_headers, _ = make_user()
        make_join_request(requester_headers, trip_id)

        r = client.delete(f"/api/announcements/{trip_id}", headers=owner_headers)
        assert r.status_code == 200

        # Detail now 404, absent from listing
        assert client.get(f"/api/announcements/{trip_id}").status_code == 404
        assert client.get(f"/api/announcements?keyword={kw}").json()["total"] == 0

        # Pending request was rejected with an explanation
        my_reqs = client.get("/api/my-join-requests", headers=requester_headers).json()
        req = next(x for x in my_reqs if x["announcement_id"] == trip_id)
        assert req["status"] == "rejected"
        assert "cancelled" in req["response_message"].lower()

        # Requester was notified
        notifs = client.get("/api/notifications", headers=requester_headers).json()["items"]
        assert any("cancelled" in n["message"].lower() for n in notifs)

    def test_delete_by_non_owner_403(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        other_headers, _ = make_user()
        r = client.delete(f"/api/announcements/{trip_id}", headers=other_headers)
        assert r.status_code == 403
        # Still visible
        assert client.get(f"/api/announcements/{trip_id}").status_code == 200

    def test_delete_twice_404(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        assert client.delete(f"/api/announcements/{trip_id}", headers=headers).status_code == 200
        assert client.delete(f"/api/announcements/{trip_id}", headers=headers).status_code == 404


class TestAnonymousPrivacy:
    def test_detail_hides_contact_info_without_auth(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.get(f"/api/announcements/{trip_id}")  # no auth
        assert r.status_code == 200
        data = r.json()
        assert data["creator_phone"] == ""
        assert data["creator_facebook"] == ""
        assert data["creator_facebook_id"] == ""

    def test_detail_shows_contact_info_with_auth(self, client, make_user, make_trip):
        owner_headers, owner = make_user()
        trip_id = make_trip(owner_headers)
        viewer_headers, _ = make_user()
        r = client.get(f"/api/announcements/{trip_id}", headers=viewer_headers)
        data = r.json()
        assert data["creator_phone"] == owner["phone"]
        assert data["creator_facebook"] == "Test User FB"
        assert data["creator_facebook_id"] == "fb.testuser"
