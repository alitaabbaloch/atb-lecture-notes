from conftest import new_session


def put(client, sid, offset, data, seq=0):
    return client.put(f"/sessions/{sid}/chunks/{seq}", content=data, headers={"X-Offset": str(offset)})


def test_create_is_idempotent(client):
    a = new_session(client, "same")
    b = new_session(client, "same", lesson="Changed title is ignored")
    assert a["sid"] == b["sid"] and a["received_bytes"] == 0
    assert client.get("/courses").json()[0]["lessons"] == 1
    assert new_session(client, "other")["sid"] != a["sid"]


def test_create_needs_client_id(client):
    assert client.post("/sessions", json={"course": "x"}).status_code == 400


def test_append_and_get(client):
    sid = new_session(client)["sid"]
    r = put(client, sid, 0, b"a" * 100)
    assert r.status_code == 200 and r.json() == {"received_bytes": 100}
    r = put(client, sid, 100, b"b" * 50, seq=1)
    assert r.json() == {"received_bytes": 150}
    got = client.get(f"/sessions/{sid}").json()
    assert got["state"] == "recording" and got["received_bytes"] == 150
    part = next((client.env["data"] / "recordings").rglob("*.part"))
    assert part.read_bytes() == b"a" * 100 + b"b" * 50


def test_rewind(client):
    sid = new_session(client)["sid"]
    put(client, sid, 0, b"a" * 100)
    r = put(client, sid, 40, b"Z" * 10)
    assert r.status_code == 200
    assert r.json() == {"received_bytes": 50, "rewound": True}
    part = next((client.env["data"] / "recordings").rglob("*.part"))
    assert part.read_bytes() == b"a" * 40 + b"Z" * 10


def test_gap_gives_409(client):
    sid = new_session(client)["sid"]
    put(client, sid, 0, b"a" * 10)
    r = put(client, sid, 25, b"x")
    assert r.status_code == 409 and r.json() == {"received_bytes": 10}


def test_missing_offset_header(client):
    sid = new_session(client)["sid"]
    assert client.put(f"/sessions/{sid}/chunks/0", content=b"x").status_code == 400
    assert put(client, "nope", 0, b"x").status_code == 404


def test_body_too_large(client):
    sid = new_session(client)["sid"]
    r = put(client, sid, 0, b"x" * (8 * 1024 * 1024 + 1))
    assert r.status_code == 413
    assert client.get(f"/sessions/{sid}").json()["received_bytes"] == 0
    assert put(client, sid, 0, b"x" * (8 * 1024 * 1024)).status_code == 200


def test_zero_byte_finish_is_empty(client):
    sid = new_session(client)["sid"]
    r = client.post(f"/sessions/{sid}/finish", json={"reason": "stopped"})
    assert r.status_code == 200
    out = r.json()
    assert out["empty"] is True and out["job_id"] is None
    assert client.get("/jobs").json() == []
    assert client.get("/courses").json()[0]["lessons"] == 0
    assert client.post(f"/sessions/{sid}/finish", json={}).json() == out
    assert put(client, sid, 0, b"x").status_code == 409


def test_finish_is_idempotent_and_blocks_chunks(client, webm20):
    sid = new_session(client)["sid"]
    put(client, sid, 0, webm20.read_bytes())
    first = client.post(f"/sessions/{sid}/finish", json={"reason": "stopped", "total_bytes": webm20.stat().st_size})
    second = client.post(f"/sessions/{sid}/finish", json={"reason": "stopped"})
    assert first.status_code == 200 and first.json() == second.json()
    assert first.json()["empty"] is False and first.json()["job_id"]
    r = put(client, sid, 0, b"x")
    assert r.status_code == 409 and r.json() == {"state": "finished"}
    assert client.get(f"/sessions/{sid}").json()["state"] == "finished"
    assert len(client.get("/jobs").json()) == 1


def test_patch_only_while_recording(client):
    sid = new_session(client)["sid"]
    r = client.patch(f"/sessions/{sid}", json={"lesson": "Renamed", "speed": 2})
    assert r.status_code == 200
    put(client, sid, 0, b"x")
    client.post(f"/sessions/{sid}/finish", json={})
    assert client.patch(f"/sessions/{sid}", json={"lesson": "Again"}).status_code == 409
    assert client.get("/sessions/unknown").status_code == 404
