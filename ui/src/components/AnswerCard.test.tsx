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

describe("AnswerCard", () => {
  it("shows the answer and the retrieval summary", () => {
    render(<AnswerCard data={data()} />);
    expect(screen.getByText("THB 468 million.")).toBeInTheDocument();
    expect(screen.getByText(/Retrieved 2 pages · 0.5s · 100 tokens/)).toBeInTheDocument();
    expect(screen.queryByText("Not in the document")).not.toBeInTheDocument();
  });

  it("lists candidates with scores and marks the ones not sent to the model", () => {
    const c = { doc: "report", type: "table", caption: "", used: true };
    render(
      <AnswerCard
        data={data({
          score_kind: "cosine",
          candidates: [
            { ...c, pages: [4], score: 0.8123 },
            { ...c, pages: [7, 8], score: 0.41, used: false },
          ],
        })}
      />,
    );
    expect(screen.getByText("2 candidates retrieved · 1 sent to the model")).toBeInTheDocument();
    expect(screen.getByText("0.812")).toBeInTheDocument();
    expect(screen.getByText(/report p.7-8 · table \(not sent\)/)).toBeInTheDocument();
    expect(screen.getByText(/cosine similarity/)).toBeInTheDocument();
  });

  it("marks a refusal", () => {
    render(<AnswerCard data={data({ refused: true, citations: [] })} />);
    expect(screen.getByText("Not in the document")).toBeInTheDocument();
  });

  it("lists removed citations", () => {
    render(<AnswerCard data={data({ rejected_citations: [{ doc: "report", page: 99 }] })} />);
    expect(screen.getByText(/Removed unverified citations: report p.99/)).toBeInTheDocument();
  });

  it("shows and hides the cited page", async () => {
    fakeServer();
    render(<AnswerCard data={data()} />);
    const chip = screen.getByRole("button", { name: "report p.4" });
    expect(chip).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(chip);
    expect(await screen.findByRole("img", { name: "report, page 4" })).toBeInTheDocument();
    expect(chip).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(chip);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(chip).toHaveAttribute("aria-pressed", "false");
  });

  it("explains when the page cannot be loaded", async () => {
    fakeServer();
    const { fetch } = globalThis;
    globalThis.fetch = async (...a: Parameters<typeof fetch>) =>
      String(a[0]).includes("/page/") ? json({ detail: "report has 3 pages" }, 404) : fetch(...a);
    render(<AnswerCard data={data()} />);
    await userEvent.click(screen.getByRole("button", { name: "report p.4" }));
    expect(await screen.findByText(/Could not load the page: report has 3 pages/)).toBeInTheDocument();
  });
});
