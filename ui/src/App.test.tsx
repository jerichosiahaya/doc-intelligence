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

describe("App without history", () => {
  it("fills the document list and hides the history sidebar", async () => {
    fakeServer({ docs: ["report", "other"] });
    render(<App />);
    expect(await screen.findByRole("option", { name: "other" })).toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument();
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
    const post = calls.find((c) => c.path === "/ask")!;
    expect(post.body).toEqual({ question: "What was net profit?", doc: "report", conversation_id: null });
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

    const posts = calls.filter((c) => c.path === "/ask");
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
    expect(screen.getByRole("button", { name: "report p.4" })).toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("THB 468 million.")).not.toBeInTheDocument();
    await ask("two?");
    await screen.findByText("THB 468 million.");
    expect(calls.filter((c) => c.path === "/ask").map((c) => c.body.conversation_id)).toEqual([null, null]);
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
