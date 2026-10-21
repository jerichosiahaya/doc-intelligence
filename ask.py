"""Usage: python ask.py "question" [--doc NAME] [--json]"""
import json
import os
import re
import sys
import time

from rag import COLLECTION, embed, llm, models, qdrant, rerank_scores, sparse

K = int(os.environ.get("TOP_K", 6))

SYSTEM = """You answer questions about company reports using ONLY the numbered excerpts.
Each excerpt starts with a label like [report_name p.4].
- Cite every fact as [report_name p.N], using the label of the excerpt that contains it.
- If the excerpts do not contain the answer, set refused=true and say what is missing. Never guess.
- If excerpts disagree (for example an erratum corrects an earlier statement), say so and cite both.
- If a rule needs data the reports do not give, say what can and cannot be concluded.
- If the question compares several reports, answer from each report and cite each one.
Reply with JSON only: {"answer": str, "citations": [{"doc": str, "page": int}], "refused": bool}"""


def retrieve(question, k=K, doc=None):
    """SEARCH=dense (default) or hybrid (dense + BM25, fused with RRF).
    RERANK=1 fetches RERANK_N candidates and keeps the k best by a local cross-encoder.
    doc limits the search to one document."""
    rerank = os.environ.get("RERANK") == "1"
    n_out = int(os.environ.get("RERANK_N", 20)) if rerank else k
    flt = models.Filter(must=[models.FieldCondition(key="doc", match=models.MatchValue(value=doc))]) if doc else None
    dense = embed([question])[0]
    if os.environ.get("SEARCH", "dense") == "hybrid":
        n = max(3 * n_out, 20)  # each side contributes n candidates before fusion
        hits = qdrant().query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=n, filter=flt),
                models.Prefetch(query=sparse([question], query=True)[0], using="bm25", limit=n, filter=flt),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=n_out,
        ).points
    else:
        hits = qdrant().query_points(COLLECTION, query=dense, using="dense", query_filter=flt, limit=n_out).points
    out = [h.payload | {"score": h.score} for h in hits]
    if rerank:
        scores = rerank_scores(question, [f"{c['caption']}\n{c['text']}" for c in out])
        for c, sc in zip(out, scores):
            c["score"] = float(sc)
        out.sort(key=lambda c: -c["score"])
    return out[:k]


def sources(chunks):
    """Unique {doc, page} pairs covered by the chunks (a merged table covers several pages)."""
    return [{"doc": d, "page": p} for d, p in dict.fromkeys((c["doc"], p) for c in chunks for p in c["pages"])]


def verify_citations(cited, retrieved):
    """Split the model's citations into those that name a retrieved page and those that do not.
    A bare page number is accepted only when the retrieved chunks come from a single document."""
    have = {(s["doc"], s["page"]) for s in retrieved}
    docs = {d for d, _ in have}
    ok, bad = [], []
    for c in cited or []:
        doc, page = (c.get("doc"), c.get("page")) if isinstance(c, dict) else (None, c)
        m = re.search(r"\d+", str(page))
        if not m:
            continue
        if doc is None and len(docs) == 1:
            doc = next(iter(docs))
        s = {"doc": doc, "page": int(m.group())}
        if s not in ok and s not in bad:
            (ok if (s["doc"], s["page"]) in have else bad).append(s)
    return ok, bad


def label(c):
    a, b = c["pages"][0], c["pages"][-1]
    return f"{c['doc']} p.{a}" if a == b else f"{c['doc']} p.{a}-{b}"


def answer(question, doc=None):
    t0 = time.time()
    ctx = retrieve(question, doc=doc)
    context = "\n\n".join(f"[{label(c)}] ({c['type']}) {c['caption']}\n{c['text']}" for c in ctx)
    r = llm().chat.completions.create(
        model=os.environ["ANSWER_MODEL"],
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {question}"},
        ],
    )
    raw = r.choices[0].message.content
    try:
        out = json.loads(re.search(r"\{.*\}", raw, re.S).group())
    except (AttributeError, json.JSONDecodeError):
        out = {"answer": raw, "citations": [], "refused": False}
    out["retrieved_sources"] = sources(ctx)
    out["retrieved_pages"] = sorted({s["page"] for s in out["retrieved_sources"]})
    out["citations"], out["rejected_citations"] = verify_citations(out.get("citations"), out["retrieved_sources"])
    out["retrieved"] = ctx
    out["seconds"] = round(time.time() - t0, 2)
    u = r.usage
    out["tokens"] = u.total_tokens if u else None
    out["prompt_tokens"] = u.prompt_tokens if u else None
    out["completion_tokens"] = u.completion_tokens if u else None
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    doc = args.pop(args.index("--doc") + 1) if "--doc" in args else None
    args = [a for a in args if a not in ("--json", "--doc")]
    if not args:
        sys.exit('usage: python ask.py "question" [--doc NAME] [--json]')
    out = answer(args[0], doc)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        cited = ", ".join(f"{s['doc']} p.{s['page']}" for s in out["citations"]) or "-"
        print(out["answer"])
        print(f"\nCited: {cited}{'  (refused)' if out['refused'] else ''}  [{out['seconds']}s]")
        if out["rejected_citations"]:
            print("Rejected citations (page was not retrieved):", out["rejected_citations"])
