import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

import server

SRC = {"doc": "report", "page": 4}
ASK = {"answer": "THB 468 million.", "citations": [SRC], "rejected_citations": [], "refused": False,
       "retrieved": [SRC], "seconds": 0.5, "tokens": 9, "conversation_id": "c1"}


class Api(list):
    """The requests the fake network saw. respond() sets what it answers."""

    def __init__(self):
        super().__init__()
        self.fn = lambda request: httpx.Response(200, json={})

    def respond(self, fn):
        self.fn = fn


@pytest.fixture
def api(monkeypatch):
    seen = Api()

    def route(request):
        seen.append(request)
        return seen.fn(request)

    monkeypatch.setattr(server, "_transport", httpx.MockTransport(route))
    monkeypatch.delenv("DOCINTEL_API_KEY", raising=False)
    monkeypatch.delenv("DOCINTEL_URL", raising=False)
    return seen


def run(coro):
    return asyncio.run(coro)


def test_ask_sends_the_question_and_keeps_only_the_useful_fields(api):
    api.respond(lambda r: httpx.Response(200, json=ASK))
    out = run(server.ask_documents("What was net profit?", doc="report", conversation_id="c0"))
    assert out == {"answer": "THB 468 million.", "refused": False, "citations": [SRC], "rejected_citations": [],
                   "conversation_id": "c1"}
    req = api[0]
    assert (req.method, req.url.path) == ("POST", "/ask")
    assert json.loads(req.content) == {"question": "What was net profit?", "doc": "report", "conversation_id": "c0"}


def test_api_key_header_only_when_set(api, monkeypatch):
    run(server.list_documents())
    assert "x-api-key" not in api[0].headers
    monkeypatch.setenv("DOCINTEL_API_KEY", "secret")
    run(server.list_documents())
    assert api[1].headers["x-api-key"] == "secret"


def test_base_url_may_carry_a_path_prefix(api, monkeypatch):
    monkeypatch.setenv("DOCINTEL_URL", "http://localhost:8080/api")
    run(server.list_documents())
    assert str(api[0].url) == "http://localhost:8080/api/documents"


def test_list_documents(api):
    api.respond(lambda r: httpx.Response(200, json=["report", "other"]))
    assert run(server.list_documents()) == ["report", "other"]


def test_page_image_returns_png_bytes_and_encodes_the_name(api):
    api.respond(lambda r: httpx.Response(200, content=b"\x89PNG...", headers={"content-type": "image/png"}))
    img = run(server.get_page_image("my/report", 3))
    assert isinstance(img, Image) and img.data == b"\x89PNG..."
    assert api[0].url.raw_path == b"/page/my%2Freport/3.png"  # a slash in the name cannot reach another route


def test_conversation_id_cannot_escape_its_path_segment(api):
    api.respond(lambda r: httpx.Response(404, json={"detail": "unknown conversation: x"}))
    with pytest.raises(ToolError):
        run(server.get_conversation("../documents"))
    assert api[0].url.raw_path == b"/conversations/..%2Fdocuments"


def test_list_conversations_trims_fields_and_limit(api):
    rows = [{"id": f"c{i}", "title": f"t{i}", "created_at": "x", "updated_at": "y", "messages": 2} for i in range(5)]
    api.respond(lambda r: httpx.Response(200, json=rows))
    out = run(server.list_conversations(limit=2))
    assert out == [{"id": "c0", "title": "t0", "updated_at": "y", "messages": 2},
                   {"id": "c1", "title": "t1", "updated_at": "y", "messages": 2}]


def test_get_conversation_keeps_citations_for_answers_only(api):
    conv = {"id": "c1", "title": "t", "created_at": "", "updated_at": "", "messages": [
        {"id": 1, "role": "user", "content": "q?", "citations": None, "retrieved": None},
        {"id": 2, "role": "assistant", "content": "a.", "citations": [SRC], "retrieved": [SRC]}]}
    api.respond(lambda r: httpx.Response(200, json=conv))
    out = run(server.get_conversation("c1"))
    assert out["messages"] == [{"role": "user", "content": "q?"},
                               {"role": "assistant", "content": "a.", "citations": [SRC]}]


@pytest.mark.parametrize("status, body, expected", [
    (401, {"detail": "invalid or missing API key"}, "DOCINTEL_API_KEY"),
    (404, {"detail": "unknown document: nope"}, "list_documents"),
    (404, {"detail": "history is not enabled (set DATABASE_URL)"}, "history is not enabled"),
    (400, {"detail": "history is not enabled (set DATABASE_URL)"}, "Do not pass conversation_id"),
    (422, {"detail": [{"msg": "Input should be a valid UUID", "loc": ["body", "conversation_id"]}]}, "valid UUID"),
    (502, {"detail": "backend error: llm down"}, "502: backend error: llm down"),
])
def test_server_errors_become_tool_errors_that_say_what_to_do(api, status, body, expected):
    api.respond(lambda r: httpx.Response(status, json=body))
    with pytest.raises(ToolError, match=expected):
        run(server.ask_documents("q"))


def test_error_without_a_json_body(api):
    api.respond(lambda r: httpx.Response(504, text="<html>"))
    with pytest.raises(ToolError, match="504"):
        run(server.list_documents())


def test_unreachable_server(api, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused")
    api.respond(refuse)
    monkeypatch.setenv("DOCINTEL_URL", "http://localhost:9")
    with pytest.raises(ToolError, match=r"Cannot reach .* http://localhost:9\. Is it running"):
        run(server.list_documents())


def test_slow_server(api, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow")
    api.respond(slow)
    monkeypatch.setenv("DOCINTEL_TIMEOUT", "5")
    with pytest.raises(ToolError, match="within 5 seconds"):
        run(server.ask_documents("q"))
