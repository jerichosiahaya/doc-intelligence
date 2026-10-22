import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ModelList } from "../types";
import { ArrowUp, Paperclip } from "./icons";

interface Props {
  docs: string[];
  doc: string;
  onDoc: (doc: string) => void;
  models: ModelList | null;
  model: string;
  onModel: (model: string) => void;
  busy: boolean;
  /** Resolves true when the question was answered; on false the text comes back into the box. */
  onAsk: (question: string) => Promise<boolean>;
  onAttach: (file: File) => void;
  autoFocus?: boolean;
}

export default function Composer({ docs, doc, onDoc, models, model, onModel, busy, onAsk, onAttach, autoFocus }: Props) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  async function submit() {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    if (!(await onAsk(q))) setText(q);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form className="composer" onSubmit={(e) => (e.preventDefault(), submit())}>
      <textarea
        ref={area}
        rows={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder="Ask anything about your documents…"
        maxLength={2000}
        aria-label="Question"
        autoFocus={autoFocus}
      />
      <div className="tools">
        <button type="button" className="icon" aria-label="Add a PDF" title="Add a PDF" onClick={() => picker.current?.click()}>
          <Paperclip />
        </button>
        <input
          ref={picker}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          aria-hidden="true"
          tabIndex={-1}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onAttach(f);
            e.target.value = "";
          }}
        />
        <select className="pill" value={doc} onChange={(e) => onDoc(e.target.value)} aria-label="Document">
          <option value="">All documents</option>
          {docs.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        {models && models.models.length > 0 && (
          <select className="pill" value={model} onChange={(e) => onModel(e.target.value)} aria-label="Model">
            <option value="">Default{models.default ? ` · ${models.default}` : ""}</option>
            {models.models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
        <span className="grow" />
        <button type="submit" className="send" aria-label="Ask" disabled={busy || !text.trim()}>
          <ArrowUp />
        </button>
      </div>
    </form>
  );
}
