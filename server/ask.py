"""Usage: python ask.py "question" [--doc NAME] [--json]"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import BadRequestError

from reqlog import log
from rag import COLLECTION, embed, llm, models, qdrant, rerank_scores, sparse

K = int(os.environ.get("TOP_K", 6))

SYSTEM = """You answer questions about company reports using ONLY the numbered excerpts.
Each excerpt starts with a label like [report_name p.4].
- Cite every fact as [report_name p.N], using the label of the excerpt that contains it.
- If the excerpts do not contain the answer, set the JSON field "refused" to true and, in "answer", say what is missing. Never guess. If the excerpts do contain the answer, "refused" is false. Never write the word "refused" inside the answer text.
- If excerpts disagree (for example an erratum corrects an earlier statement), say so and cite both.
- If a rule needs data the reports do not give, say what can and cannot be concluded. When a rule has a condition \
that the data cannot test (for example "two consecutive quarters" when only annual figures exist), say that the rule \
cannot be applied and name the missing data. You may list items that fail only the part you can test, but do not say \
that they meet the rule.
- When you count items, count each distinct item once, and list them.
- If the question compares several reports, answer from each report and cite each one.
Reply with JSON only: {"answer": str, "citations": [{"doc": str, "page": int}], "refused": bool}"""


def rerank_on():
    return os.environ.get("RERANK", "1") == "1"  # on by default: it gained 2 of 17 on the retrieval eval


def score_kind():
    """What the score of a retrieved chunk means. The scales differ, so compare scores only within one answer."""
    if rerank_on():
        return "rerank"
    return "rrf" if os.environ.get("SEARCH", "dense") == "hybrid" else "cosine"


def search(question, k=K, doc=None):
    """All candidates the search fetched, best first. Each has a score (see score_kind).
    SEARCH=dense (default) or hybrid (dense + BM25, fused with RRF).
    RERANK=1 (the default) fetches RERANK_N candidates and re-scores them with a local cross-encoder.
    doc limits the search to one document."""
    rerank = rerank_on()
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
    return out


def retrieve(question, k=K, doc=None):
    """The k best candidates: the chunks that go to the model."""
    return search(question, k, doc)[:k]


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


ROUTER_PROMPT = """You route the messages of an assistant that answers questions about the user's documents (reports in PDF form).
Reply with one word.
- rag: the message asks for facts that the documents could contain: numbers, tables, charts, policies, names, dates, comparisons, or a short topic such as "revenue 2025".
- chat: the message is only a greeting, thanks, a goodbye, small talk, or a question about the assistant itself, how to use it, or which documents it has.
If you are not sure, reply rag."""

CHAT_SYSTEM = """You are the assistant of a document question-answering system. The user's message is not a question about the documents.
Reply briefly and in a friendly way, in the language of the user. Use plain text only: no Markdown and no asterisks.
- You can say what you do: you answer questions about the ingested documents, you cite the pages, and you say so when the documents do not contain the answer.
- Documents available now: {docs}.
- Do not state any fact from the documents, and do not use your own knowledge about the company. If the user wants a fact, ask them to write it as a question."""


def route(question):
    """rag or chat. ROUTER=0 turns the router off. Any problem returns rag: a wrong rag costs one search,
    but a wrong chat would hide the documents."""
    if os.environ.get("ROUTER", "1") != "1":
        return "rag"
    t0 = time.time()
    model = os.environ.get("ROUTER_MODEL") or os.environ.get("ANSWER_MODEL")
    try:
        r = llm("answer").chat.completions.create(
            model=model, temperature=0, max_tokens=128,  # a reasoning model spends about 20 to 30 of these before it answers
            messages=[{"role": "system", "content": ROUTER_PROMPT}, {"role": "user", "content": question}])
        word = (r.choices[0].message.content or "").strip().lower()
        u = r.usage
        log(kind="route", model=model, status="ok", seconds=round(time.time() - t0, 2), route=word[:8],
            prompt_tokens=u.prompt_tokens if u else None, completion_tokens=u.completion_tokens if u else None)
    except Exception as e:
        log(kind="route", model=model, status="error", error=str(e)[:200], seconds=round(time.time() - t0, 2))
        return "rag"
    return "chat" if word.startswith("chat") else "rag"


def _chat_messages(question):
    docs = ", ".join(sorted(p.stem for p in Path("docs").glob("*.pdf"))) or "none yet"
    return [{"role": "system", "content": CHAT_SYSTEM.format(docs=docs)}, {"role": "user", "content": question}]


def _chat_result(text, u, t0, fields):
    out = {"answer": text.strip(), "citations": [], "rejected_citations": [], "refused": False,
           "retrieved_sources": [], "retrieved_pages": [], "retrieved": [], "candidates": [], "score_kind": "",
           "route": "chat", "seconds": round(time.time() - t0, 2), "tokens": u.total_tokens if u else None,
           "prompt_tokens": u.prompt_tokens if u else None, "completion_tokens": u.completion_tokens if u else None}
    log(**{**fields, "kind": "chat"}, status="ok", seconds=out["seconds"], tokens=out["tokens"],
        prompt_tokens=out["prompt_tokens"], completion_tokens=out["completion_tokens"])
    return out


def _start_search(question, doc):
    """Search while the router decides, so a document question loses no time. A chat message drops the result."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_prepare, question, doc)
    pool.shutdown(wait=False)
    return future


def _prepare(question, doc):
    """Search, then build the prompt. Returns all candidates, the K sent to the model, and the messages."""
    found = search(question, doc=doc)
    ctx = found[:K]
    context = "\n\n".join(f"[{label(c)}] ({c['type']}) {c['caption']}\n{c['text']}" for c in ctx)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Excerpts:\n{context}\n\nQuestion: {question}"},
    ]
    return found, ctx, messages


def _candidates(found):
    return [
        {"doc": c["doc"], "pages": c["pages"], "type": c["type"], "caption": c["caption"],
         "score": round(c["score"], 4), "used": i < K,
         "snippet": " ".join(re.sub(r"[|*#_-]", " ", c["text"]).split())[:110]}
        for i, c in enumerate(found)
    ]


def _stream(client, model, messages):
    try:
        return client.chat.completions.create(
            model=model, temperature=0, messages=messages, stream=True, stream_options={"include_usage": True})
    except BadRequestError:  # a provider that does not know stream_options: stream without token counts
        return client.chat.completions.create(model=model, temperature=0, messages=messages, stream=True)


def _answer_text(buf):
    """The text of the "answer" string in a JSON reply that may be cut off, and nothing after a half-written escape."""
    m = re.search(r'"answer"\s*:\s*"', buf)
    if not m:
        return ""
    i = start = m.end()
    while i < len(buf) and buf[i] != '"':
        if buf[i] == "\\":
            width = 6 if buf[i + 1 : i + 2] == "u" else 2
            if i + width > len(buf):
                break  # the escape is not complete yet
            i += width
        else:
            i += 1
    text = json.loads('"' + buf[start:i] + '"', strict=False)
    return text[:-1] if text and "\ud800" <= text[-1] <= "\udbff" else text  # wait for the low surrogate


def _finish(raw, found, ctx, u, t0, fields):
    try:
        out = json.loads(re.search(r"\{.*\}", raw, re.S).group())
    except (AttributeError, json.JSONDecodeError):
        out = {"answer": raw, "citations": [], "refused": False}
    out["retrieved_sources"] = sources(ctx)
    out["retrieved_pages"] = sorted({s["page"] for s in out["retrieved_sources"]})
    out["citations"], out["rejected_citations"] = verify_citations(out.get("citations"), out["retrieved_sources"])
    out["retrieved"] = ctx
    out["candidates"] = _candidates(found)
    out["score_kind"] = score_kind()
    out["route"] = "rag"
    out["seconds"] = round(time.time() - t0, 2)
    out["tokens"] = u.total_tokens if u else None
    out["prompt_tokens"] = u.prompt_tokens if u else None
    out["completion_tokens"] = u.completion_tokens if u else None
    log(**fields, status="ok", seconds=out["seconds"], tokens=out["tokens"], prompt_tokens=out["prompt_tokens"],
        completion_tokens=out["completion_tokens"], refused=bool(out.get("refused")),
        citations=len(out["citations"]), rejected=len(out["rejected_citations"]), retrieved=len(ctx))
    return out


def answer(question, doc=None, via="cli", model=None):
    t0 = time.time()
    model = model or os.environ["ANSWER_MODEL"]
    fields = dict(kind="ask", via=via, doc=doc, question=question[:300], model=model)
    try:
        search_job = _start_search(question, doc)
        if route(question) == "chat":
            r = llm("answer").chat.completions.create(model=model, temperature=0, messages=_chat_messages(question))
            return _chat_result(r.choices[0].message.content or "", r.usage, t0, fields)
        found, ctx, messages = search_job.result()
        r = llm("answer").chat.completions.create(model=model, temperature=0, messages=messages)
    except Exception as e:
        log(**fields, status="error", error=str(e)[:300], seconds=round(time.time() - t0, 2))
        raise
    return _finish(r.choices[0].message.content, found, ctx, r.usage, t0, fields)


def answer_stream(question, doc=None, via="cli", model=None):
    """Like answer(), but yields ("candidates", {...}) once the search is done, ("token", text) as the
    answer text is written, and last ("done", the same dict that answer() returns). The first event is
    ("route", "rag" or "chat"). A chat message has no candidates event."""
    t0 = time.time()
    model = model or os.environ["ANSWER_MODEL"]
    fields = dict(kind="ask", via=via, doc=doc, question=question[:300], model=model)
    try:
        search_job = _start_search(question, doc)
        picked = route(question)
        yield "route", picked
        if picked == "chat":
            raw, usage = "", None
            for ev in _stream(llm("answer"), model, _chat_messages(question)):
                usage = ev.usage or usage
                if ev.choices and ev.choices[0].delta.content:
                    raw += ev.choices[0].delta.content
                    yield "token", ev.choices[0].delta.content
            yield "done", _chat_result(raw, usage, t0, fields)
            return
        found, ctx, messages = search_job.result()
        yield "candidates", {"candidates": _candidates(found), "score_kind": score_kind()}
        raw, sent, usage = "", "", None
        for ev in _stream(llm("answer"), model, messages):
            usage = ev.usage or usage
            if ev.choices:
                raw += ev.choices[0].delta.content or ""
                text = _answer_text(raw)
                if len(text) > len(sent):
                    yield "token", text[len(sent):]
                    sent = text
    except Exception as e:
        log(**fields, status="error", error=str(e)[:300], seconds=round(time.time() - t0, 2))
        raise
    yield "done", _finish(raw, found, ctx, usage, t0, fields)


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
        if out["route"] == "chat":
            sys.exit(0)
        print(f"\nCited: {cited}{'  (refused)' if out['refused'] else ''}  [{out['seconds']}s]")
        if out["rejected_citations"]:
            print("Rejected citations (page was not retrieved):", out["rejected_citations"])
