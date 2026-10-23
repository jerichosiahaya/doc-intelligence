import { Fragment, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { AnswerData, Candidate, Source } from "../types";

/** One source the model read: a chunk, which can span several pages. */
interface Card {
  doc: string;
  pages: number[];
  type?: string;
  caption?: string;
  snippet?: string;
  score?: number;
}

const pageLabel = (pages: number[]) => (pages.length > 1 ? `p.${pages[0]}-${pages[pages.length - 1]}` : `p.${pages[0]}`);
const same = (a: Source, b: Source | null) => !!b && a.doc === b.doc && a.page === b.page;

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
              {c.doc} {pageLabel(c.pages)} · {c.type}
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

const CITE = /\[([^\[\]]+?)\s+p\.(\d+)(?:-\d+)?\]/g;

/** Turn "[report p.4]" in the answer into a numbered chip that opens the source card. */
function withCitations(text: string, cards: Card[], onOpen: (s: Source) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    const doc = m[1].trim();
    const page = Number(m[2]);
    const n = cards.findIndex((c) => c.doc === doc && c.pages.includes(page));
    if (n < 0) continue; // not a source we know: leave the text as the model wrote it
    out.push(text.slice(last, m.index));
    out.push(
      <button key={m.index} type="button" className="cite" aria-label={`Source ${n + 1}: ${doc} p.${page}`}
        onClick={() => onOpen({ doc, page })}>
        {n + 1}
      </button>,
    );
    last = m.index! + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

export default function AnswerCard({ data, streaming = false }: { data: AnswerData; streaming?: boolean }) {
  const [open, setOpen] = useState<Source | null>(null);
  const toggle = (s: Source) => setOpen(same(s, open) ? null : s);

  const used = data.candidates?.filter((c) => c.used);
  const cards: Card[] = used?.length
    ? used
    : data.retrieved.map((s) => ({ doc: s.doc, pages: [s.page] })); // an answer reopened from history has no candidates
  const cited = (c: Card) => data.citations.some((s) => s.doc === c.doc && c.pages.includes(s.page));
  const paragraphs = data.answer.split(/\n{2,}/);
  // a chat message has no sources. An answer reopened from history has none saved either.
  const chat = data.route === "chat" || (!streaming && !data.refused && data.retrieved.length === 0 && !data.candidates?.length);

  return (
    <article className="answer-card" aria-busy={streaming}>
      {!chat && cards.length > 0 && (
        <section aria-label="Sources">
          <h3>Sources</h3>
          <ol className="sources">
            {cards.map((c, i) => (
              <li key={i}>
                <button type="button" className={cited(c) ? "source cited" : "source"} aria-pressed={same({ doc: c.doc, page: c.pages[0] }, open)}
                  aria-label={`Source ${i + 1}: ${c.doc} ${pageLabel(c.pages)}${cited(c) ? ", cited" : ""}`}
                  onClick={() => toggle({ doc: c.doc, page: c.pages[0] })}>
                  <span className="n">{i + 1}</span>
                  <span className="what">
                    <span className="t">{c.caption || c.snippet || (c.type ? `${c.type[0].toUpperCase()}${c.type.slice(1)}` : "Page")}</span>
                    <span className="d">{c.type ? `${c.type} · ` : ""}{pageLabel(c.pages)} · {c.doc.replace(/_/g, " ")}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
      {!chat && open && (
        <div className="page">
          <PageImage source={open} />
        </div>
      )}

      <section aria-label="Answer">
        {chat ? (
          streaming && !data.answer && <span className="dots" aria-hidden="true"><i /><i /><i /></span>
        ) : (
          <h3>
            Answer
            {streaming && <span className="dots" aria-hidden="true"><i /><i /><i /></span>}
          </h3>
        )}
        {data.refused && <div className="badge">Not in the document</div>}
        {data.answer ? (
          <div className="answer">
            {paragraphs.map((p, i) => (
              <p key={i}>
                {withCitations(p, cards, toggle).map((n, j) => (
                  <Fragment key={j}>{n}</Fragment>
                ))}
                {streaming && i === paragraphs.length - 1 && <span className="caret" aria-hidden="true" />}
              </p>
            ))}
          </div>
        ) : (
          streaming && !chat && <p className="meta">{data.candidates ? "Reading the sources…" : "Searching the documents…"}</p>
        )}
        {data.rejected_citations.length > 0 && (
          <p className="meta">
            Removed unverified citations: {data.rejected_citations.map((s) => `${s.doc} p.${s.page}`).join(", ")}
          </p>
        )}
      </section>

      {!chat && data.candidates && data.candidates.length > 0 && <Candidates list={data.candidates} kind={data.score_kind ?? ""} />}
      {!streaming && chat && <p className="meta">{data.seconds}s · no search needed</p>}
      {!streaming && !chat && (
        <p className="meta">
          Retrieved {data.retrieved.length} pages · {data.seconds}s · {data.tokens} tokens
        </p>
      )}
    </article>
  );
}
