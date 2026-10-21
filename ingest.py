"""Usage: python ingest.py report.pdf [--no-vision]

Per page: native text, native tables, and a vision transcription (charts,
tiles, diagrams, scans). Chunks are dumped to chunks.jsonl and indexed in Qdrant.
"""
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

import pymupdf as fitz

from checks import check_totals, missing_numbers
from rag import COLLECTION, embed, llm, models, qdrant, sparse

VISION_PROMPT = (
    "Transcribe this report page. Output all text and numbers exactly as shown. "
    "For tables, output Markdown tables. For charts, list every labelled value "
    "(axis, category, number). For diagrams, list each node and each connection "
    "with its label. Do not summarise or add anything not on the page."
)
PROSE_MAX = 1200  # chars per prose chunk
CACHE = Path(".cache/vision")


def vision(png: bytes) -> str:
    model = os.environ["VISION_MODEL"]
    key = hashlib.sha256(model.encode() + png).hexdigest()
    f = CACHE / key
    if f.exists():
        return f.read_text()
    img = "data:image/png;base64," + base64.b64encode(png).decode()
    r = llm().chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": img}},
        ]}],
    )
    out = r.choices[0].message.content
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(out)
    return out


def md_table(rows):
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in rows]
    lines.insert(1, "|" + "---|" * len(rows[0]))
    return "\n".join(lines)


def captions(text, kind):
    return [m.strip() for m in re.findall(rf"^({kind}\s*\d+\.?[^\n]*)", text, re.M | re.I)]


def prose_chunks(text):
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) > PROSE_MAX:
            yield buf
            buf = ""
        buf = f"{buf}\n\n{para}".strip()
    if buf:
        yield buf


def main(pdf, use_vision=True):
    doc = fitz.open(pdf)
    name = Path(pdf).stem
    chunks, prev = [], None  # prev = last table chunk, for merging across pages
    for pno, page in enumerate(doc, 1):
        text = page.get_text()
        for t in prose_chunks(text):
            chunks.append(dict(doc=name, page=pno, type="prose", source="text", caption="", text=t))
        caps = captions(text, "Table")  # tables and captions both come top-to-bottom
        for i, tab in enumerate(page.find_tables().tables):
            rows = tab.extract()
            if not rows or len(rows[0]) < 2:
                continue
            if prev and prev["pages"][-1] == pno - 1 and prev["rows"][0] == rows[0]:
                prev["rows"] += rows[1:]  # continuation: same header repeated
                prev["pages"].append(pno)
                continue
            prev = dict(doc=name, page=pno, pages=[pno], type="table", source="text",
                        caption=caps[i] if i < len(caps) else "", rows=rows)
            chunks.append(prev)
        if use_vision:
            v = vision(page.get_pixmap(dpi=170).tobytes("png"))
            chunks.append(dict(doc=name, page=pno, type="vision", source="vision",
                               caption=" / ".join(captions(v, "Figure")), text=v))
    vis = {c["page"]: c["text"] for c in chunks if c["type"] == "vision"}
    warnings = []
    for c in chunks:
        if c["type"] == "table":
            rows = c.pop("rows")
            c["text"] = md_table(rows)
            where = f"p.{c['pages'][0]} {c['caption']}".strip()
            warnings += [f"{where}: {m}" for m in check_totals(rows)]
            if use_vision:  # cross-check the native table against the vision transcript
                miss = missing_numbers(c["text"], " ".join(vis.get(p, "") for p in c["pages"]))
                if miss:
                    warnings.append(f"{where}: numbers not in the vision transcript: {', '.join(miss)}")
        c.setdefault("pages", [c["page"]])  # a merged table covers several pages
    with open("chunks.jsonl", "w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    texts = [f"{c['caption']}\n{c['text']}".strip() for c in chunks]
    q = qdrant()
    q.delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[
        models.FieldCondition(key="doc", match=models.MatchValue(value=name))])))
    q.upsert(COLLECTION, [
        models.PointStruct(id=str(uuid.uuid4()), vector={"dense": d, "bm25": b}, payload=c)
        for c, d, b in zip(chunks, embed(texts), sparse(texts))
    ])
    q.close()
    json.dump(warnings, open("warnings.json", "w"), indent=2)
    for w in warnings:
        print("WARNING", w)
    print(f"{name}: {len(chunks)} chunks from {len(doc)} pages -> chunks.jsonl, qdrant_data/")


if __name__ == "__main__":
    main(sys.argv[1], "--no-vision" not in sys.argv)
