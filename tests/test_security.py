import config

EXT = f"chrome-extension://{config.manifest_extension_id()}"


def origin_of(client):
    return f"http://127.0.0.1:{client.env['port']}"


def test_wrong_host_refused(client):
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/health", headers={"Host": f"127.0.0.1:{client.env['port'] + 1}"}).status_code == 403
    assert client.get("/health", headers={"Host": f"localhost:{client.env['port']}"}).status_code == 200


def test_foreign_origin_refused_on_get_and_post(client):
    bad = {"Origin": "https://example.com"}
    assert client.get("/health", headers=bad).status_code == 403
    assert client.post("/log", json={}, headers=bad).status_code == 403
    assert client.post("/sessions", json={"client_session_id": "x"}, headers=bad).status_code == 403
    other_ext = {"Origin": "chrome-extension://" + "a" * 32}
    assert client.get("/health", headers=other_ext).status_code == 403
    assert client.get("/health", headers={"Origin": "null"}).status_code == 403
    assert client.get("/courses").status_code == 200  # and nothing was created by the refused calls


def test_extension_and_same_origin_accepted(client):
    r = client.get("/health", headers={"Origin": EXT})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == EXT
    r = client.get("/health", headers={"Origin": origin_of(client)})
    assert r.status_code == 200
    r = client.post("/log", json={"src": "t"}, headers={"Origin": EXT})
    assert r.status_code == 200


def test_preflight_only_for_allowed_origins(client):
    ask = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "x-offset"}
    ok = client.options("/sessions/x/chunks/0", headers={"Origin": EXT, **ask})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == EXT
    assert "X-Offset" in ok.headers["access-control-allow-headers"]
    assert client.options("/sessions/x/chunks/0", headers={"Origin": "https://example.com", **ask}).status_code == 403


def test_no_wildcard_cors_anywhere(client):
    sid = client.post("/sessions", json={"client_session_id": "w"}).json()["sid"]
    calls = [
        ("get", "/health", {}), ("get", "/courses", {}), ("get", "/jobs", {}), ("get", "/jobs/none", {}),
        ("get", f"/sessions/{sid}", {}), ("post", "/log", {"json": {}}), ("get", "/library", {}),
        ("post", "/admin/shutdown", {}), ("get", "/nope", {}),
    ]
    for origin in (None, EXT, origin_of(client), "https://example.com"):
        headers = {"Origin": origin} if origin else {}
        for method, path, kw in calls:
            r = getattr(client, method)(path, headers=headers, **kw)
            assert r.headers.get("access-control-allow-origin") != "*", (method, path, origin)


def test_shutdown_needs_token(client):
    called = []
    client.app.state.request_exit = lambda: called.append(1)
    assert client.post("/admin/shutdown").status_code == 403
    assert client.post("/admin/shutdown", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert called == []
    token = (client.env["app"] / "admin.token").read_text().strip()
    r = client.post("/admin/shutdown", headers={"X-Admin-Token": token})
    assert r.status_code == 200 and r.json() == {"ok": True, "stopping": True}
    assert called == [1]


def test_shutdown_refuses_while_recording_unless_forced(client):
    called = []
    client.app.state.request_exit = lambda: called.append(1)
    token = {"X-Admin-Token": (client.env["app"] / "admin.token").read_text().strip()}
    client.post("/sessions", json={"client_session_id": "busy"})
    r = client.post("/admin/shutdown", headers=token)
    assert r.status_code == 409 and r.json() == {"busy": True} and called == []
    assert client.post("/admin/shutdown?force=1", headers=token).status_code == 200
    assert called == [1]


def test_token_file_and_server_json(client):
    import json
    import stat
    token = client.env["app"] / "admin.token"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    info = json.loads((client.env["app"] / "server.json").read_text())
    assert set(info) == {"pid", "port", "nonce", "started"} and info["port"] == client.env["port"]
    health = client.get("/health").json()
    assert health["nonce"] == info["nonce"] and health["app"] == "atb-lecture-notes" and health["api"] == 2
    assert token.read_text().strip() not in client.get("/health").text


def test_dev_routes_only_in_dev_mode(client, env, monkeypatch):
    assert client.get("/dev/").status_code == 404
    assert client.get("/dev/ext/manifest.json").status_code == 404
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("ATB_DEV", "1")
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as dev:
        assert dev.get("/dev/nope.html").status_code == 404  # mounted, file missing
        assert dev.get("/dev/ext/../../pyproject.toml").status_code in (400, 404)
        assert dev.put("/dev/ext/x", content=b"x").status_code in (404, 405)
        assert dev.get("/dev/ext/manifest.json").status_code in (200, 404)  # 200 once the extension exists
