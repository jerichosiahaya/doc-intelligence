import uuid

import pytest

import history
import server

REPLY_KEYS = ("answer", "citations", "rejected_citations", "retrieved", "refused", "seconds", "tokens")


def test_conversation_id_without_history_is_400(client):
    r = client.post("/ask", json={"question": "q", "conversation_id": str(uuid.uuid4())})
    assert r.status_code == 400


def test_bad_conversation_id_is_422(client):
    assert client.post("/ask", json={"question": "q", "conversation_id": "nope"}).status_code == 422


def test_history_routes_are_404_when_off(client):
    assert client.get("/conversations").status_code == 404
    assert client.get(f"/conversations/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/conversations/{uuid.uuid4()}").status_code == 404
    assert client.get("/health").json() == {"ok": True, "history": False}


def test_failed_save_does_not_lose_the_answer(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(server.history, "enabled", lambda: True)
    monkeypatch.setattr(server.history, "save_turn", boom)
    monkeypatch.setattr(server.history, "exists", lambda cid: True)
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200 and r.json()["answer"] == "ok" and r.json()["conversation_id"] is None
    cid = str(uuid.uuid4())
    r = client.post("/ask", json={"question": "q", "conversation_id": cid})
    assert r.status_code == 200 and r.json()["conversation_id"] == cid  # the client keeps its thread


@pytest.mark.db
def test_full_flow(client, monkeypatch):
    history.init()
    monkeypatch.setattr(server.history, "enabled", lambda: True)
    assert client.get("/health").json()["history"] is True

    first = client.post("/ask", json={"question": "What was net profit?", "doc": "demo"}).json()
    cid = first["conversation_id"]
    assert cid
    second = client.post("/ask", json={"question": "And in 2023?", "conversation_id": cid}).json()
    assert second["conversation_id"] == cid
    try:
        titles = [c["id"] for c in client.get("/conversations").json()]
        assert cid in titles

        conv = client.get(f"/conversations/{cid}").json()
        assert conv["title"] == "What was net profit?"
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]
        assert conv["messages"][1]["citations"] == [{"doc": "demo", "page": 1}]

        assert client.post("/ask", json={"question": "q", "conversation_id": str(uuid.uuid4())}).status_code == 404
        assert client.get(f"/conversations/{uuid.uuid4()}").status_code == 404
        assert client.get("/conversations/not-a-uuid").status_code == 422
    finally:
        assert client.delete(f"/conversations/{cid}").status_code == 204
    assert client.get(f"/conversations/{cid}").status_code == 404


@pytest.mark.db
def test_history_routes_need_the_key(client, monkeypatch):
    monkeypatch.setattr(server.history, "enabled", lambda: True)
    monkeypatch.setattr(server, "API_KEYS", ["k"])
    assert client.get("/conversations").status_code == 401
    assert client.get("/conversations", headers={"X-API-Key": "k"}).status_code == 200
