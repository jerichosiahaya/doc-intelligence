import pymupdf
import pytest
from fastapi.testclient import TestClient

import server

SRC = {"doc": "demo", "page": 1}
REPLY = {"answer": "ok", "citations": [SRC], "rejected_citations": [], "refused": False,
         "retrieved_sources": [SRC], "seconds": 0.1, "tokens": 5}


@pytest.fixture
def client(tmp_path, monkeypatch):
    pdf = pymupdf.open()
    for i in range(2):
        pdf.new_page().insert_text((72, 72), f"page {i + 1}")
    (tmp_path / "docs").mkdir()
    pdf.save(tmp_path / "docs" / "demo.pdf")
    monkeypatch.setattr(server, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(server, "log", lambda **f: None)
    monkeypatch.setattr(server, "answer", lambda q, doc=None, via="rest": REPLY)
    return TestClient(server.app)  # no "with": the gRPC server in the lifespan stays off


def test_health_and_ui_are_open(client, monkeypatch):
    monkeypatch.setattr(server, "API_KEYS", ["secret"])
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


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
