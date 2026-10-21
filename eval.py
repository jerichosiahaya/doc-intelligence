"""Usage: python eval.py [--retrieval-only] [--doc NAME]   (reads eval.yaml, writes results.json)

Retrieval (did every supporting page come back?) and answer quality (judge model)
are scored separately. Unanswerable questions pass only if the system refuses.
--retrieval-only skips the answer and judge calls, so it needs no LLM.
"""
import json
import os
import re
import sys
from collections import defaultdict

import yaml

from ask import answer, retrieve

RETRIEVAL_ONLY = "--retrieval-only" in sys.argv
DOC = sys.argv[sys.argv.index("--doc") + 1] if "--doc" in sys.argv else None  # limit to one ingested document

JUDGE = """Compare the candidate answer with the expected answer. The candidate is correct if it
states the same key facts (wording and extra detail do not matter). Reply JSON: {"correct": bool, "why": str}"""


def judge(q, expected, got):
    from rag import llm

    r = llm().chat.completions.create(
        model=os.environ["JUDGE_MODEL"],
        temperature=0,
        messages=[
            {"role": "system", "content": JUDGE},
            {"role": "user", "content": f"Question: {q}\nExpected: {expected}\nCandidate: {got}"},
        ],
    )
    return json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group())


def norm(s):
    return s.lower().replace(",", "")


def hit(item, chunks):
    """Every supporting page came back AND every evidence string is in the retrieved text."""
    if item["expected"] == "REFUSE":
        return None
    got = {p for c in chunks for p in c["pages"]}
    text = norm(" ".join(c["text"] for c in chunks))
    return all(p in got for p in item.get("pages") or []) and all(norm(e) in text for e in item.get("evidence") or [])


def run(item):
    refuse = item["expected"] == "REFUSE"
    row = dict(id=item["id"], type=item["type"], q=item["q"], expected=item["expected"])
    if RETRIEVAL_ONLY:
        chunks = retrieve(item["q"], doc=DOC)
        row.update(retrieved_pages=sorted({p for c in chunks for p in c["pages"]}), retrieval_hit=hit(item, chunks),
                   answer_ok=None)
        return row
    a = answer(item["q"], DOC, via="eval")
    row.update(got=a["answer"], retrieved_pages=a["retrieved_pages"], seconds=a["seconds"], tokens=a["tokens"],
               retrieval_hit=hit(item, a["retrieved"]))
    if refuse:
        row["answer_ok"], row["why"] = a["refused"], "refused" if a["refused"] else "answered instead of refusing"
    else:
        j = judge(item["q"], item["expected"], a["answer"])
        row["answer_ok"], row["why"] = j["correct"], j["why"]
    return row


def fmt(vals):
    vals = [v for v in vals if v is not None]
    return f"{sum(vals)}/{len(vals)}" if vals else "-"


if __name__ == "__main__":
    rows = []
    for item in yaml.safe_load(open("eval.yaml")):
        row = run(item)
        rows.append(row)
        print(f"{row['id']:>2} {row['type']:<13} retr={row['retrieval_hit']!s:<5} ans={row['answer_ok']}")

    json.dump(rows, open("results.json", "w"), indent=2, ensure_ascii=False)
    by = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r)
    cfg = " ".join(f"{k}={os.environ.get(k, '-')}" for k in ("SEARCH", "RERANK", "TOP_K"))
    print(f"\n{cfg}\n{'type':<13}{'n':>3}{'retrieval':>11}{'answer':>8}")
    for t, rs in [*by.items(), ("ALL", rows)]:
        print(f"{t:<13}{len(rs):>3}{fmt([r['retrieval_hit'] for r in rs]):>11}"
              f"{fmt([r['answer_ok'] for r in rs]):>8}")
