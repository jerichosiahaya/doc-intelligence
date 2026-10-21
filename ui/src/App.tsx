import { useCallback, useEffect, useRef, useState } from "react";
import { api, isUnauthorized } from "./api";
import AnswerCard from "./components/AnswerCard";
import AskForm from "./components/AskForm";
import KeyBar from "./components/KeyBar";
import Sidebar from "./components/Sidebar";
import type { ConversationSummary, StoredMessage, Turn } from "./types";

const fromStored = (m: StoredMessage, id: number): Turn =>
  m.role === "user"
    ? { id, kind: "question", text: m.content }
    : {
        id,
        kind: "answer",
        data: {
          answer: m.content,
          citations: m.citations ?? [],
          rejected_citations: m.rejected_citations ?? [],
          refused: !!m.refused,
          retrieved: m.retrieved ?? [],
          seconds: m.seconds ?? 0,
          tokens: m.tokens ?? 0,
        },
      };

export default function App() {
  const [historyOn, setHistoryOn] = useState(false);
  const [docs, setDocs] = useState<string[]>([]);
  const [doc, setDoc] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [needKey, setNeedKey] = useState(false);
  const nextId = useRef(1);
  const id = () => nextId.current++;

  const guard = useCallback((e: unknown) => {
    if (isUnauthorized(e)) setNeedKey(true);
  }, []);

  const reloadList = useCallback(
    () => api.conversations().then(setConversations).catch(guard),
    [guard],
  );

  const load = useCallback(async () => {
    try {
      setHistoryOn((await api.health()).history);
      setDocs(await api.documents());
      setNeedKey(false);
    } catch (e) {
      guard(e);
    }
  }, [guard]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (historyOn) reloadList();
  }, [historyOn, reloadList]);

  function newChat() {
    setConversationId(null);
    setTurns([]);
  }

  async function openConversation(cid: string) {
    try {
      const conv = await api.conversation(cid);
      setConversationId(conv.id);
      setTurns(conv.messages.map((m) => fromStored(m, id())));
    } catch (e) {
      guard(e);
      setTurns([{ id: id(), kind: "error", message: `Could not open the conversation: ${(e as Error).message}` }]);
    }
  }

  async function deleteConversation(cid: string) {
    try {
      await api.deleteConversation(cid);
      if (cid === conversationId) newChat();
      reloadList();
    } catch (e) {
      guard(e);
    }
  }

  async function ask(question: string): Promise<boolean> {
    const pending = id();
    setTurns((t) => [...t, { id: id(), kind: "question", text: question }, { id: pending, kind: "pending" }]);
    setBusy(true);
    try {
      const d = await api.ask({ question, doc: doc || null, conversation_id: conversationId });
      setTurns((t) => t.map((x) => (x.id === pending ? { id: pending, kind: "answer", data: d } : x)));
      if (d.conversation_id) {
        setConversationId(d.conversation_id);
        reloadList();
      }
      return true;
    } catch (e) {
      guard(e);
      setTurns((t) =>
        t.map((x) => (x.id === pending ? { id: pending, kind: "error", message: `Request failed: ${(e as Error).message}` } : x)),
      );
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={historyOn ? "layout has-history" : "layout"}>
      {historyOn && (
        <Sidebar
          conversations={conversations}
          currentId={conversationId}
          onOpen={openConversation}
          onDelete={deleteConversation}
          onNew={newChat}
        />
      )}
      <main>
        <h1>Document Intelligence</h1>
        <p className="sub">
          Ask a question about the reports. Answers cite their pages and decline when the reports don't say.
        </p>
        {needKey && <KeyBar onSaved={load} />}
        <div className="thread" aria-live="polite">
          {turns.map((t) => {
            switch (t.kind) {
              case "question":
                return (
                  <div key={t.id} className="q">
                    {t.text}
                  </div>
                );
              case "pending":
                return (
                  <p key={t.id} className="meta">
                    Thinking…
                  </p>
                );
              case "answer":
                return <AnswerCard key={t.id} data={t.data} />;
              case "error":
                return (
                  <div key={t.id} className="card err">
                    {t.message}
                  </div>
                );
            }
          })}
        </div>
        <AskForm docs={docs} doc={doc} busy={busy} onDoc={setDoc} onAsk={ask} />
      </main>
    </div>
  );
}
