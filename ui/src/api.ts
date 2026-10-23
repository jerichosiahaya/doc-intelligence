import type { AskResponse, Candidate, Conversation, ConversationSummary, Job, ModelList, Stats } from "./types";

// The server is a separate service. In development Vite proxies /api; in Docker nginx does.
// Set VITE_API_URL at build time to call the server directly (it then needs CORS_ORIGINS).
const BASE = import.meta.env.VITE_API_URL ?? "/api";
const KEY = "apiKey";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export const isUnauthorized = (e: unknown) => e instanceof ApiError && e.status === 401;

export function getKey(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setKey(value: string) {
  try {
    sessionStorage.setItem(KEY, value);
  } catch {
    // private mode: the key lasts until the page closes
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const key = getKey();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (key) headers["X-API-Key"] = key;
  const r = await fetch(BASE + path, { ...init, headers });
  if (r.ok) return r;
  if (r.status === 401) throw new ApiError(401, "API key required");
  let detail = r.statusText;
  try {
    const body = await r.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // not JSON: keep the status text
  }
  throw new ApiError(r.status, detail);
}

const json = async <T>(path: string, init?: RequestInit): Promise<T> => (await request(path, init)).json();

export interface StreamHandlers {
  onRoute?: (route: "rag" | "chat") => void;
  onCandidates: (c: { candidates: Candidate[]; score_kind: string }) => void;
  onToken: (text: string) => void;
}

/** Ask with server-sent events. Resolves with the full reply when the stream ends. */
async function askStream(
  body: { question: string; doc: string | null; model: string | null; conversation_id: string | null },
  on: StreamHandlers,
): Promise<AskResponse> {
  const r = await request("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.body) throw new ApiError(502, "the browser cannot read the answer stream");
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done: AskResponse | null = null;
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    for (let end = buffer.indexOf("\n\n"); end >= 0; end = buffer.indexOf("\n\n")) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!event || data === undefined) continue;
      const payload = JSON.parse(data);
      if (event === "route") on.onRoute?.(payload.route);
      else if (event === "candidates") on.onCandidates(payload);
      else if (event === "token") on.onToken(payload.text);
      else if (event === "done") done = payload;
      else if (event === "error") throw new ApiError(502, payload.message);
    }
  }
  if (!done) throw new ApiError(502, "the answer stream ended early");
  return done;
}

export const api = {
  askStream,
  models: () => json<ModelList>("/models"),
  deleteDocument: async (name: string) => {
    await request(`/documents/${encodeURIComponent(name)}`, { method: "DELETE" });
  },
  jobs: () => json<Job[]>("/jobs"),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return json<Job>("/documents", { method: "POST", body: form });
  },
  health: () => json<{ ok: boolean; history: boolean }>("/health"),
  stats: () => json<Stats>("/stats"),
  documents: () => json<string[]>("/documents"),
  ask: (body: { question: string; doc: string | null; conversation_id: string | null }) =>
    json<AskResponse>("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  conversations: () => json<ConversationSummary[]>("/conversations"),
  conversation: (id: string) => json<Conversation>(`/conversations/${id}`),
  deleteConversation: async (id: string) => {
    await request(`/conversations/${id}`, { method: "DELETE" });
  },
  pageImage: async (doc: string, page: number) =>
    (await request(`/page/${encodeURIComponent(doc)}/${page}.png`)).blob(),
};
