import ingest


def test_md_table_shape_and_newlines():
    md = ingest.md_table([["Name", "THB"], ["Line\nhaul", "21,500"], [None, "1"]])
    lines = md.split("\n")
    assert lines[0] == "| Name | THB |"
    assert lines[1] == "|---|---|"
    assert lines[2] == "| Line haul | 21,500 |"
    assert lines[3] == "|  | 1 |"


def test_captions_in_page_order():
    text = "Intro\nTable 3. Fleet by type\nrows\nTable 4. Greenhouse gas emissions\n"
    assert ingest.captions(text, "Table") == ["Table 3. Fleet by type", "Table 4. Greenhouse gas emissions"]
    assert ingest.captions("Figure 1. Revenue by quarter", "Figure") == ["Figure 1. Revenue by quarter"]
    assert ingest.captions("no captions here", "Table") == []


def test_prose_chunks_respect_limit_and_order():
    paras = [f"Paragraph {i} " + "x" * 500 for i in range(6)]
    chunks = list(ingest.prose_chunks("\n\n".join(paras)))
    assert len(chunks) > 1
    assert all(len(c) <= ingest.PROSE_MAX + 600 for c in chunks)  # one paragraph may overshoot, never two
    assert "".join(chunks).replace("\n", "").count("Paragraph") == 6
    assert chunks[0].startswith("Paragraph 0") and chunks[-1].rstrip().endswith("x")


def test_prose_chunks_skip_blank_text():
    assert list(ingest.prose_chunks("\n\n  \n\n")) == []
