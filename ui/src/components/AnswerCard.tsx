import { useEffect, useState } from "react";
import { api } from "../api";
import type { AnswerData, Source } from "../types";

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
      <p className="meta">
        Retrieved {data.retrieved.length} pages · {data.seconds}s · {data.tokens} tokens
      </p>
    </div>
  );
}
