import { useCallback, useEffect, useRef, useState } from "react";
import { api, isUnauthorized } from "./api";
import AnswerCard from "./components/AnswerCard";
import Composer from "./components/Composer";
import DocumentsPanel from "./components/DocumentsPanel";
import { Menu } from "./components/icons";
import KeyBar from "./components/KeyBar";
import Sidebar from "./components/Sidebar";
import type { AnswerData, ConversationSummary, ModelList, Stats, StoredMessage, Turn } from "./types";
import { useUploads } from "./useUploads";

const EMPTY: AnswerData = {
  answer: "", citations: [], rejected_citations: [], refused: false, retrieved: [], seconds: 0, tokens: 0,
};

const fromStored = (m: StoredMessage, id: number): Turn =>
  m.role === "user"
    ? { id, kind: "question", text: m.content }
    : {
        id,
        kind: "answer",
        streaming: false,
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
  const [models, setModels] = useState<ModelList | null>(null);
  const [model, setModel] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [needKey, setNeedKey] = useState(false);
  const [panel, setPanel] = useState(false);
  const [nav, setNav] = useState(false);
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
      setStats(await api.stats());
      setNeedKey(false);
      api.models().then(setModels).catch(() => setModels(null)); // the list is a convenience: the proxy may be down
    } catch (e) {
      guard(e);
    }
  }, [guard]);

  const uploads = useUploads(load, useCallback(() => setNeedKey(true), []));
  const { refresh: resumeJobs } = uploads;

  useEffect(() => {
    load().then(resumeJobs);
  }, [load, resumeJobs]);

  useEffect(() => {
    if (historyOn) reloadList();
  }, [historyOn, reloadList]);

  function newChat() {
    setConversationId(null);
    setTurns([]);
    setNav(false);
  }

  async function openConversation(cid: string) {
    setNav(false);
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

  async function removeDocument(name: string) {
    try {
      await api.deleteDocument(name);
      if (doc === name) setDoc("");
      load();
    } catch (e) {
      guard(e);
    }
  }

  function attach(file: File) {
    setPanel(true);
    uploads.upload(file);
  }

  async function ask(question: string): Promise<boolean> {
    const aid = id();
    const patch = (fn: (d: AnswerData) => AnswerData) =>
      setTurns((t) => t.map((x) => (x.id === aid && x.kind === "answer" ? { ...x, data: fn(x.data) } : x)));
    setTurns((t) => [
      ...t,
      { id: id(), kind: "question", text: question },
      { id: aid, kind: "answer", streaming: true, data: EMPTY },
    ]);
    setBusy(true);
    try {
      const d = await api.askStream(
        { question, doc: doc || null, model: model || null, conversation_id: conversationId },
        {
          onCandidates: (c) => patch((a) => ({ ...a, candidates: c.candidates, score_kind: c.score_kind as AnswerData["score_kind"] })),
          onToken: (text) => patch((a) => ({ ...a, answer: a.answer + text })),
        },
      );
      setTurns((t) => t.map((x) => (x.id === aid ? { id: aid, kind: "answer", streaming: false, data: d } : x)));
      if (d.conversation_id) {
        setConversationId(d.conversation_id);
        reloadList();
      }
      return true;
    } catch (e) {
      guard(e);
      setTurns((t) =>
        t.map((x) => (x.id === aid ? { id: aid, kind: "error", message: `Request failed: ${(e as Error).message}` } : x)),
      );
      return false;
    } finally {
      setBusy(false);
    }
  }

  const empty = turns.length === 0;
  const composer = (
    <Composer
      docs={docs} doc={doc} onDoc={setDoc} models={models} model={model} onModel={setModel}
      busy={busy} onAsk={ask} onAttach={attach} autoFocus
    />
  );

  return (
    <div className="app">
      <Sidebar
        conversations={historyOn ? conversations : null}
        currentId={conversationId}
        chunks={stats ? stats.chunks : null}
        open={nav}
        onOpen={openConversation}
        onDelete={deleteConversation}
        onNew={newChat}
        onDocuments={() => (setPanel(true), setNav(false))}
      />
      {nav && <div className="scrim" onClick={() => setNav(false)} />}
      <main className={empty ? "home" : "thread-view"}>
        <button type="button" className="icon menu" aria-label="Menu" onClick={() => setNav(true)}>
          <Menu />
        </button>
        {needKey && <KeyBar onSaved={load} />}
        {empty && (
          <header className="hero">
            <h1>Ask your documents</h1>
            <p>Answers cite their pages, and decline when the documents don't say.</p>
          </header>
        )}
        {!empty && (
          <div className="thread">
            {turns.map((t) => {
              switch (t.kind) {
                case "question":
                  return <h2 key={t.id} className="q">{t.text}</h2>;
                case "answer":
                  return <AnswerCard key={t.id} data={t.data} streaming={t.streaming} />;
                case "error":
                  return <div key={t.id} className="notice err" role="alert">{t.message}</div>;
              }
            })}
          </div>
        )}
        <div className={empty ? "slot" : "slot dock"}>{composer}</div>
        {empty && stats && (
          <button type="button" className="statline" onClick={() => setPanel(true)}>
            <strong>{stats.chunks}</strong> chunks in {Object.keys(stats.documents).length}{" "}
            {Object.keys(stats.documents).length === 1 ? "document" : "documents"}
          </button>
        )}
        <p className="sr-only" role="status">{busy ? "Searching and writing the answer" : ""}</p>
      </main>
      {panel && (
        <DocumentsPanel
          stats={stats} jobs={uploads.jobs} error={uploads.error}
          onUpload={uploads.upload} onDelete={removeDocument} onClose={() => setPanel(false)}
        />
      )}
    </div>
  );
}
