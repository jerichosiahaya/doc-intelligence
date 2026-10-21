"""Usage: python ask.py "question" """
import json
import os
import re
import sys
import time

from rag import embed, llm, qdrant, COLLECTION

K = int(os.environ.get("TOP_K", 6))

SYSTEM = """You answer questions about a company report using ONLY the numbered excerpts.
- Cite the page of every fact as [p.N].
- If the excerpts do not contain the answer, set refused=true and say what is missing. Never guess.
- If excerpts disagree (e.g. an erratum corrects an earlier statement), say so and cite both pages.
- If a rule needs data the report does not give, say what can and cannot be concluded.
Reply with JSON only: {"answer": str, "citations": [page ints], "refused": bool}"""


def retrieve(question, k=K):
    hits = qdrant().query_points(COLLECTION, query=embed([question])[0], limit=k).points
    return [h.payload | {"score": h.score} for h in hits]


def answer(question):
    t0 = time.time()
    ctx = retrieve(question)
    context = "\n\n".join(
        f"[p.{c['page']}] ({c['type']}) {c['caption']}\n{c['text']}" for c in ctx
    )
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
    out["retrieved_pages"] = sorted({c["page"] for c in ctx})
    out["seconds"] = round(time.time() - t0, 2)
    out["tokens"] = r.usage.total_tokens if r.usage else None
    return out


if __name__ == "__main__":
    print(json.dumps(answer(sys.argv[1]), indent=2, ensure_ascii=False))
