"""Real local models and embedded Qdrant on a tiny generated PDF. Run with RUN_MODEL_TESTS=1."""
import pymupdf
import pytest

import ask
import ingest
import rag

pytestmark = pytest.mark.models

PAGES = {
    "fleet": ["Our electric fleet grew from 136 vehicles to 218 vehicles during the year.",
              "Net profit for the year was THB 531 million, up from THB 468 million."],
    "people": ["Women held 38 percent of management positions at the end of the year.",
               "The lost-time injury frequency rate improved to 0.42 per million hours."],
}


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    import os
    d = tmp_path_factory.mktemp("idx")
    old = os.getcwd()
    os.chdir(d)
    rag.close_qdrant()
    for name, pages in PAGES.items():
        pdf = pymupdf.open()
        for text in pages:
            pdf.new_page().insert_textbox(pymupdf.Rect(72, 72, 500, 300), text)
        pdf.save(d / f"{name}.pdf")
        ingest.main(str(d / f"{name}.pdf"), use_vision=False)
    yield d
    rag.close_qdrant()
    os.chdir(old)


def test_dense_finds_the_right_page(index):
    top = ask.retrieve("How many electric vehicles are in the fleet?", k=1)[0]
    assert top["doc"] == "fleet" and top["pages"] == [1]


def test_document_filter(index):
    docs = {c["doc"] for c in ask.retrieve("net profit", k=6, doc="people")}
    assert docs == {"people"}
    assert ask.retrieve("net profit", doc="missing") == []


def test_hybrid_and_rerank_return_k_results(index, monkeypatch):
    for search, rerank in (("hybrid", "0"), ("dense", "1"), ("hybrid", "1")):
        monkeypatch.setenv("SEARCH", search)
        monkeypatch.setenv("RERANK", rerank)
        r = ask.retrieve("What was the injury frequency rate?", k=2)
        assert len(r) == 2
        assert r[0]["doc"] == "people", (search, rerank)


def test_reingest_replaces_not_duplicates(index):
    n = rag.qdrant().count(rag.COLLECTION, exact=True).count
    ingest.main(str(index / "fleet.pdf"), use_vision=False)
    assert rag.qdrant().count(rag.COLLECTION, exact=True).count == n
