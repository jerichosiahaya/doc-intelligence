import { describe, expect, it } from "vitest";
import { api, ApiError, isUnauthorized, setKey } from "./api";
import { fakeServer, json } from "./test-helpers";

describe("api", () => {
  it("sends no key header until a key is set, then sends it", async () => {
    const { calls } = fakeServer();
    await api.documents();
    expect(calls[0].headers["X-API-Key"]).toBeUndefined();
    setKey("secret");
    await api.documents();
    expect(calls[1].headers["X-API-Key"]).toBe("secret");
  });

  it("turns 401 into an unauthorized ApiError", async () => {
    fakeServer({ requireKey: "k" });
    const err = await api.documents().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(isUnauthorized(err)).toBe(true);
  });

  it("uses the detail text of an error response", async () => {
    fakeServer({ ask: () => json({ detail: "backend error: llm down" }, 502) });
    const err = await api.ask({ question: "q", doc: null, conversation_id: null }).catch((e) => e);
    expect(err.status).toBe(502);
    expect(err.message).toBe("backend error: llm down");
  });

  it("falls back to the status text when the body is not JSON", async () => {
    fakeServer({ ask: () => new Response("<html>", { status: 504, statusText: "Gateway Timeout" }) });
    const err = await api.ask({ question: "q", doc: null, conversation_id: null }).catch((e) => e);
    expect(err.message).toBe("Gateway Timeout");
  });

  it("posts the question as JSON", async () => {
    const { calls } = fakeServer();
    await api.ask({ question: "q?", doc: "report", conversation_id: null });
    expect(calls[0]).toMatchObject({ method: "POST", path: "/ask", body: { question: "q?", doc: "report", conversation_id: null } });
    expect(calls[0].headers["Content-Type"]).toBe("application/json");
  });

  it("encodes the document name in the page image URL", async () => {
    const { calls } = fakeServer();
    await api.pageImage("my report/2025", 3);
    expect(calls[0].path).toBe("/page/my%20report%2F2025/3.png");
  });
});
