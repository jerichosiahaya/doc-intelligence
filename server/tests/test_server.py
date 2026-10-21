import server

SRC = {"doc": "demo", "page": 1}


def test_health_is_open(client, monkeypatch):
    monkeypatch.setattr(server, "API_KEYS", ["secret"])
    assert client.get("/health").status_code == 200


def test_cors_allows_only_the_configured_origin():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    other = FastAPI()
    other.get("/x")(lambda: {"ok": True})
    server.apply_cors(other, ["https://ui.example"])
    c = TestClient(other)
    ok = c.get("/x", headers={"Origin": "https://ui.example"})
    bad = c.get("/x", headers={"Origin": "https://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "https://ui.example"
    assert "access-control-allow-origin" not in bad.headers
    pre = c.options("/x", headers={"Origin": "https://ui.example", "Access-Control-Request-Method": "POST",
                                   "Access-Control-Request-Headers": "x-api-key,content-type"})
    assert pre.status_code == 200


def test_cors_is_off_by_default(client):
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "https://x.example"}).headers


def test_ask_ok(client):
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200 and r.json()["citations"] == [SRC]


def test_ask_validation_and_unknown_doc(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "q", "doc": "nope"}).status_code == 404
    assert client.post("/ask", json={"question": "q", "doc": "demo"}).status_code == 200


def test_backend_error_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(server, "answer", boom)
    assert client.post("/ask", json={"question": "q"}).status_code == 502


def test_auth(client, monkeypatch):
    monkeypatch.setattr(server, "API_KEYS", ["k1", "k2"])
    body = {"question": "q"}
    assert client.post("/ask", json=body).status_code == 401
    assert client.post("/ask", json=body, headers={"X-API-Key": "bad"}).status_code == 401
    assert client.post("/ask", json=body, headers={"X-API-Key": "k2"}).status_code == 200
    assert client.get("/documents").status_code == 401
    assert client.get("/page/demo/1.png").status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "k1"}).json() == ["demo"]


def test_key_ok_without_keys_allows_everything(monkeypatch):
    monkeypatch.setattr(server, "API_KEYS", [])
    assert server.key_ok(None) and server.key_ok("anything")


def test_page_image(client):
    r = client.get("/page/demo/2.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get("/page/demo/3.png").status_code == 404
    assert client.get("/page/demo/0.png").status_code == 404
    assert client.get("/page/nope/1.png").status_code == 404
    assert client.get("/page/..%2Fdemo/1.png").status_code == 404
