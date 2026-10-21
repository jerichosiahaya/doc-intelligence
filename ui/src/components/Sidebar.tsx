import { useState } from "react";
import type { ConversationSummary } from "../types";

interface Props {
  conversations: ConversationSummary[];
  currentId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
}

export default function Sidebar({ conversations, currentId, onOpen, onDelete, onNew }: Props) {
  const [armed, setArmed] = useState<string | null>(null); // delete needs two clicks

  return (
    <aside>
      <button type="button" className="ghost" onClick={onNew}>
        New chat
      </button>
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
                onClick={() => (armed === c.id ? (setArmed(null), onDelete(c.id)) : setArmed(c.id))}
              >
                {armed === c.id ? "Sure?" : "Delete"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
