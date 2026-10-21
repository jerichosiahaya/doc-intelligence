"""Usage: python eval.py   (reads eval.yaml, writes results.json)

Retrieval (did a supporting page come back?) and answer quality (judge model)
are scored separately. Unanswerable questions pass only if the system refuses.
"""
import json
import os
import re
from collections import defaultdict

import yaml

from ask import answer
from rag import llm

JUDGE = """Compare the candidate answer with the expected answer. The candidate is correct if it
states the same key facts (wording and extra detail do not matter). Reply JSON: {"correct": bool, "why": str}"""


def judge(q, expected, got):
    r = llm().chat.completions.create(
        model=os.environ["JUDGE_MODEL"],
        temperature=0,
        messages=[
            {"role": "system", "content": JUDGE},
            {"role": "user", "content": f"Question: {q}\nExpected: {expected}\nCandidate: {got}"},
        ],
    )
    return json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group())


rows = []
for item in yaml.safe_load(open("eval.yaml")):
    a = answer(item["q"])
    pages = item.get("pages") or []
    refuse = item["expected"] == "REFUSE"
    row = dict(id=item["id"], type=item["type"], q=item["q"], expected=item["expected"], got=a["answer"],
               retrieved_pages=a["retrieved_pages"], seconds=a["seconds"], tokens=a["tokens"])
    row["retrieval_hit"] = None if refuse else all(p in a["retrieved_pages"] for p in pages)
    if refuse:
        row["answer_ok"], row["why"] = a["refused"], "refused" if a["refused"] else "answered instead of refusing"
    else:
        j = judge(item["q"], item["expected"], a["answer"])
        row["answer_ok"], row["why"] = j["correct"], j["why"]
    rows.append(row)
    print(f"{item['id']:>2} {item['type']:<12} retr={row['retrieval_hit']!s:<5} ans={row['answer_ok']}")

json.dump(rows, open("results.json", "w"), indent=2, ensure_ascii=False)
by = defaultdict(list)
for r in rows:
    by[r["type"]].append(r)
print(f"\n{'type':<12}{'n':>3}{'retrieval':>11}{'answer':>8}")
for t, rs in by.items():
    rr = [r["retrieval_hit"] for r in rs if r["retrieval_hit"] is not None]
    print(f"{t:<12}{len(rs):>3}{(f'{sum(rr)}/{len(rr)}' if rr else '-'):>11}{sum(r['answer_ok'] for r in rs):>5}/{len(rs)}")
rr = [r["retrieval_hit"] for r in rows if r["retrieval_hit"] is not None]
print(f"{'ALL':<12}{len(rows):>3}{f'{sum(rr)}/{len(rr)}':>11}{sum(r['answer_ok'] for r in rows):>5}/{len(rows)}")
