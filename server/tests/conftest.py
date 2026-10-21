import os

import pymupdf
import pytest
from fastapi.testclient import TestClient

import server

SRC = {"doc": "demo", "page": 1}
REPLY = {"answer": "ok", "citations": [SRC], "rejected_citations": [], "refused": False,
         "retrieved_sources": [SRC], "seconds": 0.1, "tokens": 5}


def pytest_collection_modifyitems(config, items):
    skips = {
        "models": (os.environ.get("RUN_MODEL_TESTS") == "1", "set RUN_MODEL_TESTS=1 to run (downloads local models)"),
        "db": (bool(os.environ.get("DATABASE_URL")), "set DATABASE_URL to run (needs Postgres)"),
    }
    for item in items:
        for mark, (run, why) in skips.items():
            if mark in item.keywords and not run:
                item.add_marker(pytest.mark.skip(reason=why))


@pytest.fixture
def client(tmp_path, monkeypatch):
    pdf = pymupdf.open()
    for i in range(2):
        pdf.new_page().insert_text((72, 72), f"page {i + 1}")
    (tmp_path / "docs").mkdir()
    pdf.save(tmp_path / "docs" / "demo.pdf")
    monkeypatch.setattr(server, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(server, "log", lambda **f: None)
    monkeypatch.setattr(server, "answer", lambda q, doc=None, via="rest": REPLY)
    monkeypatch.setattr(server.history, "enabled", lambda: False)  # tests opt in to history explicitly
    monkeypatch.setattr(server, "API_KEYS", [])  # a stray API_KEY in the environment must not change results
    return TestClient(server.app)  # no "with": the gRPC server in the lifespan stays off
