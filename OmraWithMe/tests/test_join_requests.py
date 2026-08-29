"""Join requests: create, duplicates, capacity, respond (accept/reject), cancel."""


def _respond(client, headers, request_id, status, message=""):
    return client.post(
        f"/api/join-requests/{request_id}/respond",
        json={"request_id": request_id, "status": status, "response_message": message},
        headers=headers,
    )


class TestCreateJoinRequest:
    def test_create_success(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)
        assert req_id > 0

        # Owner is notified of the new request
        notifs = client.get("/api/notifications", headers=owner_headers).json()["items"]
        assert any(n["notif_type"] == "new_request" for n in notifs)

    def test_duplicate_request_400(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        make_join_request(requester_headers, trip_id)
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "again", "num_people": 1},
                        headers=requester_headers)
        assert r.status_code == 400
        assert "already requested" in r.json()["detail"].lower()

    def test_join_own_trip_400(self, client, make_user, make_trip):
        headers, _ = make_user()
        trip_id = make_trip(headers)
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "me too", "num_people": 1},
                        headers=headers)
        assert r.status_code == 400
        assert "own" in r.json()["detail"].lower()

    def test_capacity_exceeded_400(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers, max_participants=2)
        requester_headers, _ = make_user()
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "big group", "num_people": 3},
                        headers=requester_headers)
        assert r.status_code == 400
        assert "spots" in r.json()["detail"].lower()

    def test_join_deleted_trip_404(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        client.delete(f"/api/announcements/{trip_id}", headers=owner_headers)
        requester_headers, _ = make_user()
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "too late", "num_people": 1},
                        headers=requester_headers)
        assert r.status_code == 404

    def test_join_inactive_trip_400(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        client.patch(f"/api/announcements/{trip_id}/toggle", headers=owner_headers)
        requester_headers, _ = make_user()
        r = client.post("/api/join-requests",
                        json={"announcement_id": trip_id, "message": "hi", "num_people": 1},
                        headers=requester_headers)
        assert r.status_code == 400


class TestRespond:
    def test_owner_accept_updates_spots_and_notifies(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers, max_participants=10)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id, num_people=4)

        r = _respond(client, owner_headers, req_id, "accepted", "Welcome aboard")
        assert r.status_code == 200

        detail = client.get(f"/api/announcements/{trip_id}").json()
        assert detail["spots_filled"] == 4
        assert detail["spots_available"] == 6

        # Requester sees the accepted status and is notified
        my_reqs = client.get("/api/my-join-requests", headers=requester_headers).json()
        req = next(x for x in my_reqs if x["id"] == req_id)
        assert req["status"] == "accepted"
        assert req["response_message"] == "Welcome aboard"

        notifs = client.get("/api/notifications", headers=requester_headers).json()["items"]
        assert any(n["notif_type"] == "accepted" for n in notifs)

    def test_owner_reject(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)

        r = _respond(client, owner_headers, req_id, "rejected", "Sorry, full family group")
        assert r.status_code == 200

        detail = client.get(f"/api/announcements/{trip_id}").json()
        assert detail["spots_filled"] == 0  # reject must not consume spots

        my_reqs = client.get("/api/my-join-requests", headers=requester_headers).json()
        req = next(x for x in my_reqs if x["id"] == req_id)
        assert req["status"] == "rejected"

    def test_non_owner_respond_403(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)

        # The requester themselves cannot accept their own request
        r = _respond(client, requester_headers, req_id, "accepted")
        assert r.status_code == 403
        # Nor can a random third user
        third_headers, _ = make_user()
        r = _respond(client, third_headers, req_id, "accepted")
        assert r.status_code == 403

    def test_respond_twice_400(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)
        assert _respond(client, owner_headers, req_id, "rejected").status_code == 200
        assert _respond(client, owner_headers, req_id, "accepted").status_code == 400

    def test_accept_exceeding_remaining_capacity_400(
        self, client, make_user, make_trip, make_join_request
    ):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers, max_participants=10)
        req_a_headers, _ = make_user()
        req_a = make_join_request(req_a_headers, trip_id, num_people=4)
        req_b_headers, _ = make_user()
        req_b = make_join_request(req_b_headers, trip_id, num_people=7)  # 0+7 <= 10 at request time

        assert _respond(client, owner_headers, req_a, "accepted").status_code == 200
        # 4 + 7 > 10 — accepting B must now fail
        r = _respond(client, owner_headers, req_b, "accepted")
        assert r.status_code == 400


class TestCancel:
    def test_requester_cancels_pending(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)

        r = client.delete(f"/api/join-requests/{req_id}", headers=requester_headers)
        assert r.status_code == 200
        my_reqs = client.get("/api/my-join-requests", headers=requester_headers).json()
        assert all(x["id"] != req_id for x in my_reqs)

    def test_cancel_by_non_requester_403(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)
        r = client.delete(f"/api/join-requests/{req_id}", headers=owner_headers)
        assert r.status_code == 403

    def test_cancel_accepted_request_400(self, client, make_user, make_trip, make_join_request):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        requester_headers, _ = make_user()
        req_id = make_join_request(requester_headers, trip_id)
        _respond(client, owner_headers, req_id, "accepted")
        r = client.delete(f"/api/join-requests/{req_id}", headers=requester_headers)
        assert r.status_code == 400
