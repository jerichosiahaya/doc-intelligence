import json
import shutil
from pathlib import Path

import pymupdf
from conftest import REPLY
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


def test_stats_counts_chunks_per_document(client, monkeypatch):
    class Q:
        def count(self, collection, count_filter=None, exact=True):
            return type("R", (), {"count": 7 if count_filter is None else 3})()

    monkeypatch.setattr(server, "qdrant", lambda: Q())
    assert client.get("/stats").json() == {"chunks": 7, "documents": {"demo": 3}}


def test_ask_reply_carries_candidates_and_grpc_ignores_them(client, monkeypatch):
    cand = {"doc": "demo", "pages": [1], "type": "prose", "caption": "", "score": 0.8, "used": True, "snippet": "x"}
    monkeypatch.setattr(server, "answer", lambda q, doc=None, via="rest", model=None: {**REPLY, "candidates": [cand], "score_kind": "cosine"})
    r = client.post("/ask", json={"question": "q"}).json()
    assert r["candidates"] == [cand] and r["score_kind"] == "cosine"
    server.pb.AskReply(**server.ask("q").model_dump(exclude={"candidates", "score_kind"}, mode="python") | {"conversation_id": ""})


def _models(monkeypatch, ids=("m1", "m2")):
    fake = type("L", (), {"models": type("M", (), {"list": lambda self: [type("X", (), {"id": i})() for i in ids]})()})
    monkeypatch.setattr(server, "llm", lambda: fake)
    monkeypatch.setattr(server, "_models", (0.0, []))


def test_models_lists_ids_and_default(client, monkeypatch):
    _models(monkeypatch, ("b", "a"))
    monkeypatch.setenv("ANSWER_MODEL", "a")
    assert client.get("/models").json() == {"default": "a", "models": ["a", "b"]}


def test_ask_passes_a_known_model_and_rejects_an_unknown_one(client, monkeypatch):
    _models(monkeypatch)
    seen = {}
    monkeypatch.setattr(server, "answer", lambda q, doc=None, via="rest", model=None: seen.update(model=model) or REPLY)
    assert client.post("/ask", json={"question": "q", "model": "m2"}).status_code == 200
    assert seen["model"] == "m2"
    r = client.post("/ask", json={"question": "q", "model": "nope"})
    assert r.status_code == 404 and "unknown model" in r.json()["detail"]


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev, data = block.split("\n")
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_stream_sends_candidates_tokens_then_the_full_reply(client, monkeypatch):
    def fake(q, doc=None, via="rest", model=None):
        yield "candidates", {"candidates": [], "score_kind": "cosine"}
        yield "token", "Hel"
        yield "token", "lo"
        yield "done", {**REPLY, "answer": "Hello"}

    monkeypatch.setattr(server, "answer_stream", fake)
    r = client.post("/ask/stream", json={"question": "q"})
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    assert [e for e, _ in ev] == ["candidates", "token", "token", "done"]
    assert "".join(d["text"] for e, d in ev if e == "token") == "Hello"
    assert ev[-1][1]["answer"] == "Hello" and ev[-1][1]["citations"] == [SRC]


def test_stream_reports_a_failure_as_an_event_and_checks_input_first(client, monkeypatch):
    def boom(*a, **k):
        yield "candidates", {"candidates": [], "score_kind": ""}
        raise RuntimeError("proxy down")

    monkeypatch.setattr(server, "answer_stream", boom)
    ev = _events(client.post("/ask/stream", json={"question": "q"}).text)
    assert ev[-1][0] == "error" and "proxy down" in ev[-1][1]["message"]
    assert client.post("/ask/stream", json={"question": "q", "doc": "nope"}).status_code == 404


class _Now:
    def submit(self, fn, *a):
        fn(*a)


def _pdf_bytes(pages=1):
    d = pymupdf.open()
    for _ in range(pages):
        d.new_page()
    return d.tobytes()


def test_upload_ingests_the_pdf_and_reports_the_job(client, monkeypatch):
    seen = {}

    def fake_main(pdf, progress=None, release=True):
        seen.update(name=Path(pdf).name, release=release)
        progress("reading", 1, 1)
        return 9

    monkeypatch.setattr(server.ingest, "main", fake_main)
    monkeypatch.setattr(server, "_ingest_pool", _Now())
    r = client.post("/documents", files={"file": ("My Report (v2).pdf", _pdf_bytes(), "application/pdf")})
    assert r.status_code == 202
    job = client.get(f"/jobs/{r.json()['id']}").json()
    assert (job["status"], job["chunks"], job["name"]) == ("done", 9, "My_Report_v2")
    assert seen == {"name": "My_Report_v2.pdf", "release": False}  # keeps the shared Qdrant client open
    assert client.get("/jobs/missing").status_code == 404


def test_upload_failure_is_reported_and_leaves_no_new_document(client, monkeypatch):
    def fake_main(pdf, progress=None, release=True):
        shutil.copy(pdf, server.DOCS / Path(pdf).name)  # ingest copies the file before it can fail
        raise RuntimeError("vision failed")

    monkeypatch.setattr(server.ingest, "main", fake_main)
    monkeypatch.setattr(server, "_ingest_pool", _Now())
    r = client.post("/documents", files={"file": ("new.pdf", _pdf_bytes(), "application/pdf")})
    job = client.get(f"/jobs/{r.json()['id']}").json()
    assert job["status"] == "error" and "vision failed" in job["error"]
    assert "new" not in client.get("/documents").json()


def test_upload_rejects_bad_files(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD", 2000)
    monkeypatch.setattr(server, "MAX_PAGES", 2)
    post = lambda name, data: client.post("/documents", files={"file": (name, data, "application/pdf")})
    assert post("a.pdf", b"not a pdf").status_code == 400
    assert post("a.pdf", b"%PDF-1.4 broken").status_code == 400
    assert post("a.pdf", _pdf_bytes(3)).status_code == 400  # too many pages
    assert post("a.pdf", b"%PDF-" + b"x" * 3000).status_code == 413
    assert post("...", _pdf_bytes()).status_code == 400  # no usable name


def test_delete_removes_chunks_and_pdf_and_refuses_unknown_or_busy(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "qdrant", lambda: type("Q", (), {"delete": lambda self, c, sel: calls.append(sel)})())
    server.JOBS["j"] = {"name": "demo", "status": "running"}
    assert client.delete("/documents/demo").status_code == 409 and not calls
    server.JOBS.clear()
    assert client.delete("/documents/nope").status_code == 404
    assert client.delete("/documents/demo").status_code == 204
    assert len(calls) == 1 and client.get("/documents").json() == []
