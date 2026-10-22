import { useCallback, useEffect, useRef, useState } from "react";
import { api, isUnauthorized } from "./api";
import type { Job } from "./types";

const busy = (j: Job) => j.status === "queued" || j.status === "running";

/** Upload PDFs and follow their ingest jobs. onDone runs when a job finishes, so the caller can reload the counts. */
export function useUploads(onDone: () => void, onUnauthorized: () => void) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Job[]>([]);
  const active = jobs.some(busy);

  const update = useCallback(
    (next: Job[]) => {
      const finished = next.some((j) => j.status === "done" && seen.current.some((p) => p.id === j.id && p.status !== "done"));
      seen.current = next;
      setJobs(next);
      if (finished) onDone();
    },
    [onDone],
  );

  // a reload during an ingest picks the job up again
  const refresh = useCallback(
    () => api.jobs().then((list) => update(list.slice().reverse())).catch((e) => isUnauthorized(e) && onUnauthorized()),
    [update, onUnauthorized],
  );

  useEffect(() => {
    if (!active) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [active, refresh]);

  const upload = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const job = await api.upload(file);
        update([job, ...seen.current.filter((j) => j.id !== job.id)]);
        if (job.status === "done") onDone();
      } catch (e) {
        if (isUnauthorized(e)) onUnauthorized();
        setError((e as Error).message);
      }
    },
    [update, onDone, onUnauthorized],
  );

  return { jobs, error, upload, refresh, dismissError: () => setError(null) };
}
