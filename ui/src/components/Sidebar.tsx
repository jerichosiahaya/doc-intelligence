import { useState } from "react";
import type { ConversationSummary } from "../types";
import { Layers, Plus } from "./icons";

interface Props {
  /** null when the server keeps no history */
  conversations: ConversationSummary[] | null;
  currentId: string | null;
  chunks: number | null;
  open: boolean;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
  onDocuments: () => void;
}

export default function Sidebar({ conversations, currentId, chunks, open, onOpen, onDelete, onNew, onDocuments }: Props) {
  const [armed, setArmed] = useState<string | null>(null); // delete needs two clicks

  return (
    <aside className={open ? "rail open" : "rail"} aria-label="Navigation">
      <div className="brand">Document Intelligence</div>
      <button type="button" className="new" onClick={onNew}>
        <Plus /> New thread
      </button>
      <button type="button" className="nav" onClick={onDocuments}>
        <Layers /> Documents
        {chunks !== null && <span className="count">{chunks} chunks</span>}
      </button>
      {conversations && (
        <>
          <h2>History</h2>
          {conversations.length === 0 ? (
            <p className="empty">No saved conversations yet.</p>
          ) : (
            <ul>
              {conversations.map((c) => (
                <li key={c.id} aria-current={c.id === currentId}>
                  <button type="button" className="open" title={c.title} onClick={() => onOpen(c.id)}>
                    {c.title}
                  </button>
                  <button
                    type="button"
                    className={armed === c.id ? "del sure" : "del"}
                    aria-label={armed === c.id ? "Sure?" : "Delete"}
                    onClick={() => (armed === c.id ? (setArmed(null), onDelete(c.id)) : setArmed(c.id))}
                    onBlur={() => setArmed(null)}
                  >
                    {armed === c.id ? "Sure?" : "×"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}
