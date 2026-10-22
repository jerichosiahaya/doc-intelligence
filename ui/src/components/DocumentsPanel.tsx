import { useEffect, useRef, useState } from "react";
import type { Job, Stats } from "../types";
import { Close, File } from "./icons";

interface Props {
  stats: Stats | null;
  jobs: Job[];
  error: string | null;
  maxMb?: number;
  onUpload: (file: File) => void;
  onDelete: (name: string) => void;
  onClose: () => void;
}

function progress(j: Job): string {
  if (j.status === "queued") return "Waiting";
  if (j.status === "error") return `Failed: ${j.error ?? "unknown error"}`;
  if (j.status === "done") return `Done · ${j.chunks} chunks${j.replaces ? " (replaced the earlier version)" : ""}`;
  return j.stage === "indexing" ? "Indexing" : `Reading page ${j.done} of ${j.total}`;
}

export default function DocumentsPanel({ stats, jobs, error, onUpload, onDelete, onClose }: Props) {
  const [over, setOver] = useState(false);
  const [armed, setArmed] = useState<string | null>(null); // removing needs two clicks
  const picker = useRef<HTMLInputElement>(null);
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    close.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const docs = Object.entries(stats?.documents ?? {});

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="docs-title">
        <header>
          <h2 id="docs-title">Documents</h2>
          <button ref={close} type="button" className="icon" aria-label="Close" onClick={onClose}>
            <Close />
          </button>
        </header>
        <p className="meta">
          {stats ? `${stats.chunks} chunks in the vector database.` : "Loading…"}
        </p>
        <ul className="doclist">
          {docs.map(([name, n]) => (
            <li key={name}>
              <File /> <span className="name">{name}</span>
              <span className="count">{n} chunks</span>
              <button
                type="button"
                className={armed === name ? "remove sure" : "remove"}
                aria-label={armed === name ? `Sure? Remove ${name}` : `Remove ${name}`}
                onClick={() => (armed === name ? (setArmed(null), onDelete(name)) : setArmed(name))}
                onBlur={() => setArmed(null)}
              >
                {armed === name ? "Sure?" : "Remove"}
              </button>
            </li>
          ))}
          {docs.length === 0 && <li className="empty">No documents yet.</li>}
        </ul>

        <div
          className={over ? "drop over" : "drop"}
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const f = e.dataTransfer.files[0];
            if (f) onUpload(f);
          }}
        >
          <p>Drop a PDF here, or</p>
          <button type="button" className="ghost" onClick={() => picker.current?.click()}>
            Choose a file
          </button>
          <input
            ref={picker}
            type="file"
            accept="application/pdf,.pdf"
            hidden
            aria-label="PDF file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = "";
            }}
          />
          <p className="meta">A vision model reads every page, so a 13-page report takes about two minutes.</p>
        </div>
        {error && <p className="notice err" role="alert">{error}</p>}

        {jobs.length > 0 && (
          <ul className="jobs" aria-label="Uploads">
            {jobs.slice(0, 5).map((j) => (
              <li key={j.id} className={j.status}>
                <div className="row">
                  <span className="name">{j.name}</span>
                  <span className="state">{progress(j)}</span>
                </div>
                {(j.status === "running" || j.status === "queued") && (
                  <div className="track" role="progressbar" aria-valuemin={0} aria-valuemax={j.total} aria-valuenow={j.done}>
                    <span style={{ width: `${j.total ? Math.round((j.done / j.total) * 100) : 0}%` }} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
