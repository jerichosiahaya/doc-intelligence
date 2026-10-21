import { useState, type FormEvent } from "react";

interface Props {
  docs: string[];
  doc: string;
  busy: boolean;
  onDoc: (doc: string) => void;
  /** Resolves true when the question was answered, so the box can be cleared. */
  onAsk: (question: string) => Promise<boolean>;
}

export default function AskForm({ docs, doc, busy, onDoc, onAsk }: Props) {
  const [question, setQuestion] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    if (await onAsk(question)) setQuestion("");
  }

  return (
    <form className="ask" onSubmit={submit}>
      <input
        className="q-input"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="e.g. What was net profit in 2024?"
        maxLength={2000}
        aria-label="Question"
        autoFocus
        required
      />
      <select value={doc} onChange={(e) => onDoc(e.target.value)} aria-label="Document">
        <option value="">All documents</option>
        {docs.map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>
      <button disabled={busy}>Ask</button>
    </form>
  );
}
