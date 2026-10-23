import { vi } from "vitest";
import type { AskResponse, Conversation, ConversationSummary, Job, ModelList, Stats } from "./types";

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
  stats?: Stats;
  models?: ModelList;
  jobs?: Job[];
  upload?: (file: File) => Job | Response;
  conversations?: ConversationSummary[];
  conversation?: Conversation;
  ask?: (body: { question: string; doc: string | null; model: string | null; conversation_id: string | null }) => Response | AskResponse;
  requireKey?: string;
}

/** The server-sent events of POST /ask/stream for a finished answer: the words arrive one by one. */
export function sse(a: AskResponse): Response {
  const ev = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
  let out = "";
  if (a.route) out += ev("route", { route: a.route });
  if (a.candidates) out += ev("candidates", { candidates: a.candidates, score_kind: a.score_kind ?? "" });
  for (const w of a.answer.split(/(?<= )/)) out += ev("token", { text: w });
  out += ev("done", a);
  return new Response(out, { status: 200, headers: { "Content-Type": "text/event-stream" } });
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
      const body = typeof init.body === "string" ? JSON.parse(init.body) : null;
      calls.push({ method, path, body, headers });

      if (path === "/health") return json({ ok: true, history: !!o.history });
      if (o.requireKey && headers["X-API-Key"] !== o.requireKey) return json({ detail: "invalid or missing API key" }, 401);
      if (path === "/stats") return json(o.stats ?? { chunks: 0, documents: {} });
      if (path === "/documents" && method === "POST") {
        const r = o.upload?.(init.body instanceof FormData ? (init.body.get("file") as File) : (null as never));
        return r instanceof Response ? r : json(r ?? { detail: "no upload handler" }, r ? 202 : 500);
      }
      if (path.startsWith("/documents/") && method === "DELETE") {
        calls.at(-1)!.path = path;
        return new Response(null, { status: 204 });
      }
      if (path === "/documents") return json(o.docs ?? []);
      if (path === "/models") return json(o.models ?? { default: null, models: [] });
      if (path === "/jobs") return json(o.jobs ?? []);
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
      if (path === "/ask/stream") {
        const r = o.ask ? o.ask(body) : answer();
        return r instanceof Response ? r : sse(r);
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
