import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnswerData, Candidate, Source } from "../types";

const label = (s: Source) => `${s.doc} p.${s.page}`;

function PageImage({ source }: { source: Source }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    setSrc(null);
    setError(null);
    api
      .pageImage(source.doc, source.page)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [source.doc, source.page]);

  if (error) return <p className="meta">Could not load the page: {error}</p>;
  if (!src) return <p className="meta">Loading page…</p>;
  return <img src={src} alt={`${source.doc}, page ${source.page}`} />;
}

const KIND: Record<string, string> = {
  cosine: "cosine similarity, 0 to 1",
  rrf: "fused rank score (RRF)",
  rerank: "cross-encoder score",
};

/** Cosine scores are absolute. The other scales are not, so their bars are relative to this answer's candidates. */
function bar(c: Candidate, all: Candidate[], kind: string): number {
  if (kind === "cosine") return Math.max(0, Math.min(1, c.score));
  const lo = Math.min(...all.map((x) => x.score));
  const hi = Math.max(...all.map((x) => x.score));
  return hi === lo ? 1 : (c.score - lo) / (hi - lo);
}

function Candidates({ list, kind }: { list: Candidate[]; kind: string }) {
  const sent = list.filter((c) => c.used).length;
  return (
    <details className="cands">
      <summary>
        {list.length} candidates retrieved · {sent} sent to the model
      </summary>
      <p className="meta">Relevancy: {KIND[kind] ?? "score"}. Higher is more relevant.</p>
      <ol>
        {list.map((c, i) => (
          <li key={i} className={c.used ? "" : "cut"}>
            <span className="where">
              {c.doc} p.{c.pages.length > 1 ? `${c.pages[0]}-${c.pages[c.pages.length - 1]}` : c.pages[0]} · {c.type}
              {c.caption ? ` · ${c.caption}` : ""}
              {c.used ? "" : " (not sent)"}
            </span>
            <span className="track" aria-hidden="true">
              <span style={{ width: `${Math.round(bar(c, list, kind) * 100)}%` }} />
            </span>
            <span className="score">{c.score.toFixed(3)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export default function AnswerCard({ data }: { data: AnswerData }) {
  const [open, setOpen] = useState<Source | null>(null);
  const same = (a: Source, b: Source | null) => !!b && a.doc === b.doc && a.page === b.page;

  return (
    <div className="card">
      {data.refused && <div className="badge">Not in the document</div>}
      <p className="answer">{data.answer}</p>
      {data.citations.map((s) => (
        <button
          key={label(s)}
          type="button"
          className="chip"
          aria-pressed={same(s, open)}
          onClick={() => setOpen(same(s, open) ? null : s)}
        >
          {label(s)}
        </button>
      ))}
      {open && (
        <div className="page">
          <PageImage source={open} />
        </div>
      )}
      {data.rejected_citations.length > 0 && (
        <p className="meta">Removed unverified citations: {data.rejected_citations.map(label).join(", ")}</p>
      )}
      {data.candidates && data.candidates.length > 0 && (
        <Candidates list={data.candidates} kind={data.score_kind ?? ""} />
      )}
      <p className="meta">
        Retrieved {data.retrieved.length} pages · {data.seconds}s · {data.tokens} tokens
      </p>
    </div>
  );
}
