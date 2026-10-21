import type { AskResponse, Conversation, ConversationSummary } from "./types";

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

export const api = {
  health: () => json<{ ok: boolean; history: boolean }>("/health"),
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
