"""Blocking and chat messages between requester and trip creator."""
from conftest import unique_token


class TestBlocking:
    def test_block_hides_trips_and_unblock_restores(self, client, make_user, make_trip):
        creator_headers, creator = make_user()
        kw = unique_token()
        trip_id = make_trip(creator_headers, title=f"Blocked trip {kw}")
        viewer_headers, _ = make_user()

        # Visible before blocking
        r = client.get(f"/api/announcements?keyword={kw}", headers=viewer_headers)
        assert trip_id in {i["id"] for i in r.json()["items"]}

        # Viewer blocks the creator -> creator's trips hidden from viewer's listing
        r = client.post(f"/api/users/{creator['id']}/block",
                        json={"reason": "spam"}, headers=viewer_headers)
        assert r.status_code == 200
        r = client.get(f"/api/announcements?keyword={kw}", headers=viewer_headers)
        assert r.json()["total"] == 0

        # Anonymous listing is unaffected
        r = client.get(f"/api/announcements?keyword={kw}")
        assert trip_id in {i["id"] for i in r.json()["items"]}

        # Unblock restores visibility
        r = client.delete(f"/api/users/{creator['id']}/block", headers=viewer_headers)
        assert r.status_code == 200
        r = client.get(f"/api/announcements?keyword={kw}", headers=viewer_headers)
        assert trip_id in {i["id"] for i in r.json()["items"]}

    def test_join_blocked_creator_403(self, client, make_user, make_trip):
        creator_headers, creator = make_user()
        trip_id = make_trip(creator_headers)
        requester_headers, _ = make_user()
        client.post(f"/api/users/{creator['id']}/block", json={"reason": ""},
                    headers=requester_headers)

        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "hi", "num_people": 1},
                        headers=requester_headers)
        assert r.status_code == 403

    def test_blocker_trips_hidden_from_blocked_user(self, client, make_user, make_trip):
        creator_headers, _ = make_user()
        kw = unique_token()
        trip_id = make_trip(creator_headers, title=f"Mutual hide {kw}")
        other_headers, other = make_user()

        # The CREATOR blocks the other user -> creator's trips hidden from that user too
        r = client.post(f"/api/users/{other['id']}/block", json={"reason": ""},
                        headers=creator_headers)
        assert r.status_code == 200
        r = client.get(f"/api/announcements?keyword={kw}", headers=other_headers)
        assert r.json()["total"] == 0

    def test_block_self_400(self, client, make_user):
        headers, user = make_user()
        r = client.post(f"/api/users/{user['id']}/block", json={"reason": "no"}, headers=headers)
        assert r.status_code == 400

    def test_block_is_idempotent(self, client, make_user):
        headers, _ = make_user()
        _, target = make_user()
        assert client.post(f"/api/users/{target['id']}/block", json={"reason": ""},
                           headers=headers).status_code == 200
        r = client.post(f"/api/users/{target['id']}/block", json={"reason": ""}, headers=headers)
        assert r.status_code == 200
        assert r.json().get("already_blocked") is True


class TestMessages:
    def _setup_chat(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)
        return owner_headers, requester_headers, req_id

    def test_requester_and_owner_can_chat(self, client, make_user, make_trip, make_join_request):
        owner_headers, requester_headers, req_id = self._setup_chat(
            client, make_user, make_trip, make_join_request)

        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "Salam, any spots for my brother too?"},
                        headers=requester_headers)
        assert r.status_code == 200

        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "Yes of course, welcome!"},
                        headers=owner_headers)
        assert r.status_code == 200

        # Both parties read the full thread in order
        for headers in (owner_headers, requester_headers):
            msgs = client.get(f"/api/requests/{req_id}/messages", headers=headers).json()
            assert [m["content"] for m in msgs] == [
                "Salam, any spots for my brother too?",
                "Yes of course, welcome!",
            ]

    def test_third_party_cannot_read_or_post(self, client, make_user, make_trip, make_join_request):
        owner_headers, requester_headers, req_id = self._setup_chat(
            client, make_user, make_trip, make_join_request)
        client.post(f"/api/requests/{req_id}/messages",
                    json={"content": "private message"}, headers=requester_headers)

        stranger_headers, _ = make_user()
        assert client.get(f"/api/requests/{req_id}/messages",
                          headers=stranger_headers).status_code == 403
        assert client.post(f"/api/requests/{req_id}/messages",
                           json={"content": "let me in"},
                           headers=stranger_headers).status_code == 403

    def test_empty_message_400(self, client, make_user, make_trip, make_join_request):
        _, requester_headers, req_id = self._setup_chat(
            client, make_user, make_trip, make_join_request)
        r = client.post(f"/api/requests/{req_id}/messages",
                        json={"content": "   "}, headers=requester_headers)
        assert r.status_code == 400

    def test_message_notifies_recipient(self, client, make_user, make_trip, make_join_request):
        owner_headers, requester_headers, req_id = self._setup_chat(
            client, make_user, make_trip, make_join_request)
        client.post(f"/api/requests/{req_id}/messages",
                    json={"content": "ping"}, headers=requester_headers)
        notifs = client.get("/api/notifications", headers=owner_headers).json()["items"]
        assert any(n["notif_type"] == "new_message" for n in notifs)
