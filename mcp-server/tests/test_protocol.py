"""Start the real server over stdio and talk to it with the SDK client, against a tiny fake REST API."""
import asyncio
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"
SRC = {"doc": "report", "page": 4}
PNG = b"\x89PNG\r\n\x1a\nfake"
KEY = "k"


class Fake(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def reply(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        Fake.seen.append((self.command, self.path, body))
        if self.headers.get("X-API-Key") != KEY:
            return self.reply(401, {"detail": "invalid or missing API key"})
        if self.path == "/documents":
            return self.reply(200, ["report"])
        if self.path.startswith("/page/report/"):
            return self.reply(200, PNG, "image/png")
        if self.path == "/ask":
            if body.get("doc") not in (None, "report"):
                return self.reply(404, {"detail": f"unknown document: {body['doc']}"})
            return self.reply(200, {"answer": "THB 468 million.", "citations": [SRC], "rejected_citations": [],
                                    "refused": False, "retrieved": [SRC], "seconds": 0.1, "tokens": 5,
                                    "conversation_id": "c1"})
        self.reply(404, {"detail": "not found"})

    do_GET = do_POST = handle_any


@pytest.fixture
def rest():
    Fake.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


async def with_session(url, key, fn):
    env = {**os.environ, "DOCINTEL_URL": url, "DOCINTEL_API_KEY": key}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await fn(session, init)


def test_handshake_tools_and_annotations(rest):
    async def check(session, init):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        return init, tools

    init, tools = asyncio.run(with_session(rest, KEY, check))
    assert init.server_info.name == "doc-intelligence"
    assert "does NOT use earlier turns" in init.instructions
    assert set(tools) == {"ask_documents", "list_documents", "get_page_image", "list_conversations", "get_conversation"}
    assert tools["ask_documents"].input_schema["required"] == ["question"]
    assert tools["ask_documents"].annotations.read_only_hint is False
    assert all(tools[n].annotations.read_only_hint for n in tools if n != "ask_documents")


def test_ask_returns_structured_content_and_sends_the_key(rest):
    async def check(session, init):
        return await session.call_tool("ask_documents", {"question": "net profit?", "doc": "report"})

    result = asyncio.run(with_session(rest, KEY, check))
    assert not result.is_error
    assert result.structured_content["answer"] == "THB 468 million."
    assert result.structured_content["citations"] == [SRC]
    assert Fake.seen[-1] == ("POST", "/ask", {"question": "net profit?", "doc": "report", "conversation_id": None})


def test_page_image_arrives_as_an_image(rest):
    async def check(session, init):
        return await session.call_tool("get_page_image", {"doc": "report", "page": 4})

    result = asyncio.run(with_session(rest, KEY, check))
    (block,) = result.content
    assert block.type == "image" and block.mime_type == "image/png"
    assert base64.b64decode(block.data) == PNG


def test_errors_come_back_as_tool_errors_not_crashes(rest):
    async def check(session, init):
        bad_key = await session.call_tool("list_documents", {})
        return bad_key

    result = asyncio.run(with_session(rest, "wrong", check))
    assert result.is_error
    assert "DOCINTEL_API_KEY" in result.content[0].text


def test_unknown_document_and_bad_arguments(rest):
    async def check(session, init):
        unknown = await session.call_tool("ask_documents", {"question": "q", "doc": "nope"})
        bad_page = await session.call_tool("get_page_image", {"doc": "report", "page": 0})
        return unknown, bad_page

    unknown, bad_page = asyncio.run(with_session(rest, KEY, check))
    assert unknown.is_error and "list_documents" in unknown.content[0].text
    assert bad_page.is_error  # page must be at least 1
