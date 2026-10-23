import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServer, json } from "../test-helpers";
import type { AnswerData } from "../types";
import AnswerCard from "./AnswerCard";

const data = (over: Partial<AnswerData> = {}): AnswerData => ({
  answer: "THB 468 million.",
  citations: [{ doc: "report", page: 4 }],
  rejected_citations: [],
  refused: false,
  retrieved: [{ doc: "report", page: 4 }, { doc: "report", page: 5 }],
  seconds: 0.5,
  tokens: 100,
  ...over,
});

const cand = { doc: "report", type: "table", caption: "", used: true };

describe("AnswerCard", () => {
  it("shows the answer and the retrieval summary", () => {
    render(<AnswerCard data={data()} />);
    expect(screen.getByText("THB 468 million.")).toBeInTheDocument();
    expect(screen.getByText(/Retrieved 2 pages · 0.5s · 100 tokens/)).toBeInTheDocument();
    expect(screen.queryByText("Not in the document")).not.toBeInTheDocument();
  });

  it("lists candidates with scores and marks the ones not sent to the model", () => {
    render(
      <AnswerCard
        data={data({
          score_kind: "cosine",
          candidates: [
            { ...cand, pages: [4], score: 0.8123 },
            { ...cand, pages: [7, 8], score: 0.41, used: false },
          ],
        })}
      />,
    );
    expect(screen.getByText("2 candidates retrieved · 1 sent to the model")).toBeInTheDocument();
    expect(screen.getByText("0.812")).toBeInTheDocument();
    expect(screen.getByText(/report p.7-8 · table \(not sent\)/)).toBeInTheDocument();
    expect(screen.getByText(/cosine similarity/)).toBeInTheDocument();
    // only the chunks that went to the model are sources
    expect(screen.getAllByRole("button", { name: /^Source \d/ })).toHaveLength(1);
  });

  it("shows a chat reply without sources, candidates, or the retrieval footer", () => {
    render(<AnswerCard data={data({ answer: "Hello! Ask me about your documents.", route: "chat", citations: [], retrieved: [] })} />);
    expect(screen.getByText(/Hello! Ask me/)).toBeInTheDocument();
    expect(screen.queryByText("Sources")).not.toBeInTheDocument();
    expect(screen.queryByText("Answer")).not.toBeInTheDocument();
    expect(screen.queryByText(/candidates retrieved/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Retrieved/)).not.toBeInTheDocument();
    expect(screen.getByText(/no search needed/)).toBeInTheDocument();
  });

  it("does not say it is searching while a chat reply is on its way", () => {
    render(<AnswerCard streaming data={data({ answer: "", route: "chat", retrieved: [] })} />);
    expect(screen.queryByText("Searching the documents…")).not.toBeInTheDocument();
  });

  it("marks a refusal", () => {
    render(<AnswerCard data={data({ refused: true, citations: [] })} />);
    expect(screen.getByText("Not in the document")).toBeInTheDocument();
  });

  it("lists removed citations", () => {
    render(<AnswerCard data={data({ rejected_citations: [{ doc: "report", page: 99 }] })} />);
    expect(screen.getByText(/Removed unverified citations: report p.99/)).toBeInTheDocument();
  });

  it("turns a citation in the text into a numbered chip, and leaves an unknown one alone", () => {
    render(
      <AnswerCard
        data={data({
          answer: "Profit was 468 [report p.5]. Odd claim [report p.99].",
          candidates: [
            { ...cand, pages: [4], score: 0.9 },
            { ...cand, pages: [5], score: 0.8 },
          ],
        })}
      />,
    );
    expect(screen.getAllByRole("button", { name: "Source 2: report p.5" })).toHaveLength(2); // the card and the chip
    expect(screen.getByText(/Odd claim \[report p.99\]/)).toBeInTheDocument();
  });

  it("marks the sources the answer cites", () => {
    render(<AnswerCard data={data({ candidates: [{ ...cand, pages: [4], score: 0.9 }, { ...cand, pages: [5], score: 0.8 }] })} />);
    expect(screen.getByRole("button", { name: "Source 1: report p.4, cited" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Source 2: report p.5" })).toBeInTheDocument();
  });

  it("shows a cursor and the search state while the answer streams", () => {
    const { container, rerender } = render(<AnswerCard streaming data={data({ answer: "" })} />);
    expect(screen.getByText("Searching the documents…")).toBeInTheDocument();
    rerender(<AnswerCard streaming data={data({ answer: "THB 4" })} />);
    expect(container.querySelector(".caret")).not.toBeNull();
    expect(screen.queryByText(/Retrieved/)).not.toBeInTheDocument();
    rerender(<AnswerCard data={data({ answer: "THB 468" })} />);
    expect(container.querySelector(".caret")).toBeNull();
  });

  it("shows and hides the page of a source", async () => {
    fakeServer();
    render(<AnswerCard data={data()} />);
    const card = screen.getByRole("button", { name: "Source 1: report p.4, cited" });
    expect(card).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(card);
    expect(await screen.findByRole("img", { name: "report, page 4" })).toBeInTheDocument();
    expect(card).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(card);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(card).toHaveAttribute("aria-pressed", "false");
  });

  it("explains when the page cannot be loaded", async () => {
    fakeServer();
    const { fetch } = globalThis;
    globalThis.fetch = async (...a: Parameters<typeof fetch>) =>
      String(a[0]).includes("/page/") ? json({ detail: "report has 3 pages" }, 404) : fetch(...a);
    render(<AnswerCard data={data()} />);
    await userEvent.click(screen.getByRole("button", { name: "Source 1: report p.4, cited" }));
    expect(await screen.findByText(/Could not load the page: report has 3 pages/)).toBeInTheDocument();
  });
});
