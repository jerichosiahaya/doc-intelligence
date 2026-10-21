"""MCP server for Document Intelligence. Runs over stdio.

A thin client: every tool calls the REST API of the server service, so that service must be running.
Settings (environment variables):
  DOCINTEL_URL      Base URL of the REST API. Default http://localhost:8000.
                    Through the UI container, use http://localhost:8080/api.
  DOCINTEL_API_KEY  The server's API key, if the server has one.
  DOCINTEL_TIMEOUT  Seconds to wait for a reply. Default 180 (an answer calls a language model).

stdout carries the protocol. Write logs to stderr only.
"""
import logging
import os
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

INSTRUCTIONS = """Answers questions about company reports (PDF files) that were ingested into Document Intelligence.
- Use ask_documents for any question about the reports. Every answer cites pages. If the reports do not contain the answer, \
refused is true: tell the user that, and do not fill the gap from your own knowledge.
- Use get_page_image to look at a cited page (tables, charts, scans).
- The server saves conversations but does NOT use earlier turns as context. Write every question in full, \
even when you pass a conversation_id."""

logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request is noise in client logs

mcp = MCPServer("doc-intelligence", instructions=INSTRUCTIONS)

_transport: httpx.AsyncBaseTransport | None = None  # tests replace this with a mock


def _settings() -> tuple[str, str, float]:
    return (
        os.environ.get("DOCINTEL_URL", "http://localhost:8000"),
        os.environ.get("DOCINTEL_API_KEY", ""),
        float(os.environ.get("DOCINTEL_TIMEOUT", 180)),
    )


def _segment(value: str) -> str:
    """Make a value safe as one URL path segment: a slash in it cannot reach another route."""
    return quote(value, safe="")


def _explain(r: httpx.Response) -> str:
    """Turn an error response into a message that tells the model what to do next."""
    try:
        detail = r.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # validation errors
        detail = "; ".join(str(d.get("msg", d)) for d in detail)
    detail = str(detail or r.reason_phrase)
    if r.status_code == 401:
        return "The server rejected the API key. Set DOCINTEL_API_KEY to a key that the server accepts."
    if r.status_code == 404 and "history is not enabled" in detail:
        return "Conversation history is not enabled on the server (it has no DATABASE_URL)."
    if r.status_code == 400 and "history is not enabled" in detail:
        return "Do not pass conversation_id: conversation history is not enabled on the server."
    if r.status_code == 404 and "unknown document" in detail:
        return f"{detail}. Call list_documents to see the document names."
    return f"The server returned {r.status_code}: {detail}"


async def _call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    base, key, timeout = _settings()
    headers = {"X-API-Key": key} if key else {}
    try:
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=timeout, transport=_transport) as c:
            r = await c.request(method, path, **kwargs)
    except httpx.ConnectError:
        raise ToolError(f"Cannot reach the Document Intelligence server at {base}. Is it running? Set DOCINTEL_URL.")
    except httpx.TimeoutException:
        raise ToolError(f"The server did not reply within {timeout:g} seconds. Set DOCINTEL_TIMEOUT to wait longer.")
    except httpx.HTTPError as e:
        raise ToolError(f"The request to the server failed: {e}")
    if not r.is_success:
        raise ToolError(_explain(r))
    return r


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
)
async def ask_documents(
    question: Annotated[str, Field(description="A complete question about the reports, 1 to 2000 characters.")],
    doc: Annotated[str | None, Field(description="Name of one document to search. Omit to search all documents.")] = None,
    conversation_id: Annotated[
        str | None,
        Field(description="Id from an earlier answer, to save this turn in the same conversation. Omit to start a new one."),
    ] = None,
) -> dict[str, Any]:
    """Ask a question about the ingested reports.

    Returns the answer, the cited pages ({doc, page}), and refused=true when the reports do not contain the answer.
    rejected_citations lists pages the answer model cited but that were not in the retrieved text; do not rely on them.
    The turn is saved in conversation history when the server has history turned on."""
    # some clients send "" for an argument they leave out; the server would reject "" as an id
    body = {"question": question, "doc": doc or None, "conversation_id": conversation_id or None}
    r = await _call("POST", "/ask", json=body)
    d = r.json()
    return {
        "answer": d["answer"],
        "refused": d["refused"],
        "citations": d["citations"],
        "rejected_citations": d["rejected_citations"],
        "conversation_id": d.get("conversation_id"),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def list_documents() -> list[str]:
    """List the names of the ingested documents. Use a name as the doc argument of ask_documents or get_page_image."""
    return (await _call("GET", "/documents")).json()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def get_page_image(
    doc: Annotated[str, Field(description="Document name, from list_documents or a citation.")],
    page: Annotated[int, Field(ge=1, description="Page number, starting at 1.")],
) -> Image:
    """Show one page of a document as an image. Use it to check a citation, or to read a chart or scanned page."""
    r = await _call("GET", f"/page/{_segment(doc)}/{page}.png")
    return Image(data=r.content, format="png")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def list_conversations(
    limit: Annotated[int, Field(ge=1, le=50, description="How many to return, newest first.")] = 20,
) -> list[dict[str, Any]]:
    """List saved conversations, newest first. Needs conversation history to be on at the server."""
    rows = (await _call("GET", "/conversations")).json()[:limit]
    return [{k: row[k] for k in ("id", "title", "updated_at", "messages")} for row in rows]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def get_conversation(
    conversation_id: Annotated[str, Field(description="Conversation id, from list_conversations or ask_documents.")],
) -> dict[str, Any]:
    """Read one saved conversation: its questions, answers, and the pages each answer cited."""
    c = (await _call("GET", f"/conversations/{_segment(conversation_id)}")).json()
    return {
        "id": c["id"],
        "title": c["title"],
        "messages": [
            {"role": m["role"], "content": m["content"], **({"citations": m["citations"]} if m["role"] == "assistant" else {})}
            for m in c["messages"]
        ],
    }


if __name__ == "__main__":
    mcp.run()
