import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "./App";
import { answer, fakeServer, json } from "./test-helpers";
import type { Conversation, ConversationSummary } from "./types";

const summary = (id: string, title: string): ConversationSummary => ({
  id, title, created_at: "2026-10-21T00:00:00Z", updated_at: "2026-10-21T00:00:00Z", messages: 2,
});

const ask = async (q: string) => {
  await userEvent.type(screen.getByLabelText("Question"), q);
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
};

const pdf = () => new File(["%PDF-1.4"], "new report.pdf", { type: "application/pdf" });

describe("App without history", () => {
  it("fills the document list and hides the history sidebar", async () => {
    fakeServer({ docs: ["report", "other"] });
    render(<App />);
    expect(await screen.findByRole("option", { name: "other" })).toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument();
  });

  it("shows how many chunks the vector database holds", async () => {
    fakeServer({ stats: { chunks: 31, documents: { report: 31 } } });
    render(<App />);
    expect(await screen.findByText(/chunks in 1 document/)).toBeInTheDocument();
    expect(screen.getByText("31")).toBeInTheDocument();
    expect(screen.getByText("31 chunks")).toBeInTheDocument(); // in the navigation too
  });

  it("asks a question, shows the answer, and clears the box", async () => {
    const { calls } = fakeServer({ docs: ["report"] });
    render(<App />);
    await screen.findByRole("option", { name: "report" });
    await userEvent.selectOptions(screen.getByLabelText("Document"), "report");
    await ask("What was net profit?");

    expect(await screen.findByText("THB 468 million.")).toBeInTheDocument();
    expect(screen.getByText("What was net profit?")).toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("");
    const post = calls.find((c) => c.path === "/ask/stream")!;
    expect(post.body).toEqual({ question: "What was net profit?", doc: "report", model: null, conversation_id: null });
  });

  it("shows the error and keeps the question when the request fails", async () => {
    fakeServer({ ask: () => json({ detail: "backend error: llm down" }, 502) });
    render(<App />);
    await ask("q?");
    expect(await screen.findByText(/Request failed: backend error: llm down/)).toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("q?");
  });
});

describe("App with history", () => {
  it("continues the same conversation and updates the list", async () => {
    const { calls, conversations } = fakeServer({
      history: true,
      ask: (b) => {
        if (!b.conversation_id) conversations.push(summary("c1", b.question));
        return answer({ conversation_id: "c1" });
      },
    });
    render(<App />);
    expect(await screen.findByText("No saved conversations yet.")).toBeInTheDocument();

    await ask("first?");
    expect(await screen.findByRole("button", { name: "first?" })).toBeInTheDocument();
    await ask("second?");
    await screen.findAllByText("THB 468 million.");

    const posts = calls.filter((c) => c.path === "/ask/stream");
    expect(posts.map((p) => p.body.conversation_id)).toEqual([null, "c1"]);
  });

  it("reopens a saved conversation", async () => {
    const conversation: Conversation = {
      id: "c1", title: "net profit", created_at: "", updated_at: "",
      messages: [
        { id: 1, role: "user", content: "What was net profit?", doc: null, citations: null, rejected_citations: null, retrieved: null, refused: null, seconds: null, tokens: null, created_at: "" },
        { id: 2, role: "assistant", content: "THB 468 million.", doc: null, citations: [{ doc: "report", page: 4 }], rejected_citations: [], retrieved: [{ doc: "report", page: 4 }], refused: false, seconds: 1.5, tokens: 90, created_at: "" },
      ],
    };
    fakeServer({ history: true, conversations: [summary("c1", "net profit")], conversation });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "net profit" }));

    expect(await screen.findByText("What was net profit?")).toBeInTheDocument();
    expect(screen.getByText("THB 468 million.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source 1: report p.4, cited" })).toBeInTheDocument();
  });

  it("asks twice before it deletes, then clears the open conversation", async () => {
    const conversation: Conversation = { id: "c1", title: "t", created_at: "", updated_at: "", messages: [] };
    const { calls } = fakeServer({ history: true, conversations: [summary("c1", "to delete")], conversation });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "to delete" }));
    const li = screen.getByRole("button", { name: "to delete" }).closest("li")!;

    await userEvent.click(within(li).getByRole("button", { name: "Delete" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(within(li).getByRole("button", { name: "Sure?" }));

    expect(await screen.findByText("No saved conversations yet.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/conversations/c1"]);
  });

  it("New chat starts a new conversation", async () => {
    const { calls } = fakeServer({ history: true, ask: () => answer({ conversation_id: "c1" }) });
    render(<App />);
    await ask("one?");
    await screen.findByText("THB 468 million.");
    await userEvent.click(screen.getByRole("button", { name: "New thread" }));
    expect(screen.queryByText("THB 468 million.")).not.toBeInTheDocument();
    await ask("two?");
    await screen.findByText("THB 468 million.");
    expect(calls.filter((c) => c.path === "/ask/stream").map((c) => c.body.conversation_id)).toEqual([null, null]);
  });
});

describe("App streaming", () => {
  it("shows the sources first and the answer word by word, then the final text", async () => {
    const cand = { doc: "report", pages: [4], type: "table", caption: "Table 1", score: 0.82, used: true };
    fakeServer({ ask: () => answer({ answer: "Net profit was 468 [report p.4].", candidates: [cand], score_kind: "cosine" }) });
    render(<App />);
    await ask("net profit?");
    expect(await screen.findByRole("button", { name: "Source 1: report p.4" })).toBeInTheDocument();
    expect(await screen.findByText(/Net profit was 468/)).toBeInTheDocument();
    expect(await screen.findByText(/Retrieved 1 pages/)).toBeInTheDocument(); // the footer appears when the stream ends
  });

  it("shows the failure and gives the question back when the stream sends an error", async () => {
    const err = 'event: error\ndata: {"message": "backend error: llm down"}\n\n';
    fakeServer({ ask: () => new Response(err, { status: 200 }) });
    render(<App />);
    await ask("q?");
    expect(await screen.findByText(/Request failed: backend error: llm down/)).toBeInTheDocument();
    expect(screen.getByLabelText("Question")).toHaveValue("q?");
  });
});

describe("App routing", () => {
  it("shows a reply to a greeting with no sources and no candidates", async () => {
    fakeServer({ ask: () => answer({ answer: "Hello! Ask me about your documents.", route: "chat", citations: [], retrieved: [] }) });
    render(<App />);
    await ask("hey");
    expect(await screen.findByText(/Hello! Ask me about/)).toBeInTheDocument();
    expect(await screen.findByText(/no search needed/)).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
    expect(screen.queryByText(/candidates retrieved/)).not.toBeInTheDocument();
  });
});

describe("App models", () => {
  it("lists the models and sends the chosen one", async () => {
    const { calls } = fakeServer({ models: { default: "m1", models: ["m1", "m2"] } });
    render(<App />);
    await screen.findByRole("option", { name: "m2" });
    expect(screen.getByRole("option", { name: "Default · m1" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Model"), "m2");
    await ask("q?");
    await screen.findByText("THB 468 million.");
    expect(calls.find((c) => c.path === "/ask/stream")!.body.model).toBe("m2");
  });

  it("hides the model list when the server returns none", async () => {
    fakeServer();
    render(<App />);
    await screen.findByLabelText("Document");
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
  });
});

describe("App documents", () => {
  const job = (over = {}) => ({
    id: "j1", name: "new_report", status: "done" as const, stage: "indexing", done: 2, total: 2, chunks: 9, error: null, replaces: false, ...over,
  });

  it("uploads a PDF from the panel and reloads the counts", async () => {
    const { calls } = fakeServer({ upload: () => job() });
    render(<App />);
    await screen.findByLabelText("Document");
    await userEvent.click(screen.getByRole("button", { name: /Documents/ }));
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());

    expect(await screen.findByText("Done · 9 chunks")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST" && c.path === "/documents")).toHaveLength(1);
    expect(calls.filter((c) => c.path === "/stats").length).toBeGreaterThan(1); // reloaded after the job finished
  });

  it("the paperclip in the box uploads and opens the panel", async () => {
    const { calls } = fakeServer({ upload: () => job({ status: "running", stage: "reading", done: 1, total: 2, chunks: null }) });
    render(<App />);
    await screen.findByLabelText("Document");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, pdf());

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Reading page 1 of 2")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/documents")).toBe(true);
  });

  it("shows why the server refused the file", async () => {
    fakeServer({ upload: () => json({ detail: "the file is not a PDF" }, 400) });
    render(<App />);
    await screen.findByLabelText("Document");
    await userEvent.click(screen.getByRole("button", { name: /Documents/ }));
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());
    expect(await screen.findByRole("alert")).toHaveTextContent("the file is not a PDF");
  });

  it("removes a document only after a second click", async () => {
    const { calls } = fakeServer({ stats: { chunks: 5, documents: { old_report: 5 } } });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /Documents/ }));
    await userEvent.click(screen.getByRole("button", { name: "Remove old_report" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Sure? Remove old_report" }));
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/documents/old_report"]);
  });

  it("closes with Escape", async () => {
    fakeServer();
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /Documents/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("App with an API key", () => {
  it("asks for the key, then loads and sends it", async () => {
    const { calls } = fakeServer({ requireKey: "secret", docs: ["report"] });
    render(<App />);
    const field = await screen.findByLabelText("API key");
    await userEvent.type(field, "secret");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("option", { name: "report" })).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/documents").at(-1)!.headers["X-API-Key"]).toBe("secret");
  });
});
