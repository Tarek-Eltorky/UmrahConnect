"""Comments: create, edit permissions, delete permissions (author / trip owner / stranger)."""


def _comment(client, headers, trip_id, content="Nice trip!"):
    r = client.post("/api/comments",
                    json={"announcement_id": trip_id, "content": content},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _detail_comments(client, trip_id):
    return client.get(f"/api/announcements/{trip_id}").json()["comments"]


class TestComments:
    def test_create_comment_appears_in_detail(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, commenter = make_user()
        cid = _comment(client, commenter_headers, trip_id, "Is breakfast included?")

        comments = _detail_comments(client, trip_id)
        c = next(x for x in comments if x["id"] == cid)
        assert c["content"] == "Is breakfast included?"
        assert c["author_id"] == commenter["id"]

    def test_comment_on_unknown_announcement_404(self, client, make_user):
        headers, _ = make_user()
        r = client.post("/api/comments",
                        json={"announcement_id": 99999999, "content": "hello"},
                        headers=headers)
        assert r.status_code == 404

    def test_edit_by_author(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, _ = make_user()
        cid = _comment(client, commenter_headers, trip_id)

        r = client.put(f"/api/comments/{cid}", json={"content": "Edited text"},
                       headers=commenter_headers)
        assert r.status_code == 200
        c = next(x for x in _detail_comments(client, trip_id) if x["id"] == cid)
        assert c["content"] == "Edited text"

    def test_edit_by_other_user_403(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, _ = make_user()
        cid = _comment(client, commenter_headers, trip_id)

        # Even the trip owner cannot EDIT someone else's comment
        r = client.put(f"/api/comments/{cid}", json={"content": "Hijacked"},
                       headers=owner_headers)
        assert r.status_code == 403

    def test_delete_by_author(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, _ = make_user()
        cid = _comment(client, commenter_headers, trip_id)

        r = client.delete(f"/api/comments/{cid}", headers=commenter_headers)
        assert r.status_code == 200
        assert all(x["id"] != cid for x in _detail_comments(client, trip_id))

    def test_delete_by_trip_owner_allowed(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, _ = make_user()
        cid = _comment(client, commenter_headers, trip_id, "spam spam spam")

        r = client.delete(f"/api/comments/{cid}", headers=owner_headers)
        assert r.status_code == 200
        assert all(x["id"] != cid for x in _detail_comments(client, trip_id))

    def test_delete_by_unrelated_user_403(self, client, make_user, make_trip):
        owner_headers, _ = make_user()
        trip_id = make_trip(owner_headers)
        commenter_headers, _ = make_user()
        cid = _comment(client, commenter_headers, trip_id)

        stranger_headers, _ = make_user()
        r = client.delete(f"/api/comments/{cid}", headers=stranger_headers)
        assert r.status_code == 403
        assert any(x["id"] == cid for x in _detail_comments(client, trip_id))
