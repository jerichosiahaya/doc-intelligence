export interface Source {
  doc: string;
  page: number;
}

/** One chunk the search fetched. score is relevancy; its scale depends on score_kind. */
export interface Candidate {
  doc: string;
  pages: number[];
  type: string;
  caption: string;
  score: number;
  used: boolean;
  snippet?: string;
}

export interface Stats {
  chunks: number;
  documents: Record<string, number>;
}

/** What the UI needs to draw one answer. */
export interface AnswerData {
  answer: string;
  citations: Source[];
  rejected_citations: Source[];
  refused: boolean;
  retrieved: Source[];
  seconds: number;
  tokens: number;
  /** Absent for answers loaded from history: the server does not save them. */
  candidates?: Candidate[];
  score_kind?: "cosine" | "rrf" | "rerank" | "";
}

export interface AskResponse extends AnswerData {
  conversation_id: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: number;
}

export interface StoredMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  doc: string | null;
  citations: Source[] | null;
  rejected_citations: Source[] | null;
  retrieved: Source[] | null;
  refused: boolean | null;
  seconds: number | null;
  tokens: number | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: StoredMessage[];
}

export interface ModelList {
  default: string | null;
  models: string[];
}

export interface Job {
  id: string;
  name: string;
  status: "queued" | "running" | "done" | "error";
  stage: string;
  done: number;
  total: number;
  chunks: number | null;
  error: string | null;
  replaces: boolean;
}

export type Turn =
  | { id: number; kind: "question"; text: string }
  | { id: number; kind: "answer"; data: AnswerData; streaming: boolean }
  | { id: number; kind: "error"; message: string };
