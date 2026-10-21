import { vi } from "vitest";
import type { AskResponse, Conversation, ConversationSummary } from "./types";

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const answer = (over: Partial<AskResponse> = {}): AskResponse => ({
  answer: "THB 468 million.",
  citations: [{ doc: "report", page: 4 }],
  rejected_citations: [],
  refused: false,
  retrieved: [{ doc: "report", page: 4 }],
  seconds: 0.5,
  tokens: 100,
  conversation_id: null,
  ...over,
});

interface Options {
  history?: boolean;
  docs?: string[];
  conversations?: ConversationSummary[];
  conversation?: Conversation;
  ask?: (body: { question: string; doc: string | null; conversation_id: string | null }) => Response | AskResponse;
  requireKey?: string;
}

export interface Call {
  method: string;
  path: string;
  body: any;
  headers: Record<string, string>;
}

/** Replaces fetch with an in-memory fake of the server's REST API. */
export function fakeServer(o: Options = {}) {
  const calls: Call[] = [];
  const conversations = [...(o.conversations ?? [])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const path = url.replace(/^\/api/, "");
      const headers = (init.headers ?? {}) as Record<string, string>;
      const body = init.body ? JSON.parse(init.body as string) : null;
      calls.push({ method, path, body, headers });

      if (path === "/health") return json({ ok: true, history: !!o.history });
      if (o.requireKey && headers["X-API-Key"] !== o.requireKey) return json({ detail: "invalid or missing API key" }, 401);
      if (path === "/documents") return json(o.docs ?? []);
      if (path === "/conversations") return json(conversations);
      if (path.startsWith("/conversations/")) {
        const id = path.split("/")[2];
        if (method === "DELETE") {
          const i = conversations.findIndex((c) => c.id === id);
          if (i >= 0) conversations.splice(i, 1);
          return new Response(null, { status: 204 });
        }
        return o.conversation ? json(o.conversation) : json({ detail: "unknown conversation" }, 404);
      }
      if (path === "/ask") {
        const r = o.ask ? o.ask(body) : answer();
        return r instanceof Response ? r : json(r);
      }
      // a string body: Node's Response cannot read the jsdom Blob
      if (path.startsWith("/page/")) return new Response("png", { status: 200 });
      return json({ detail: "not found" }, 404);
    }),
  );
  return { calls, conversations };
}
