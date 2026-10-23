import json
from types import SimpleNamespace

import pytest

import ask

D = "report"
SRC = [{"doc": D, "page": 4}, {"doc": D, "page": 5}]


def test_verify_keeps_retrieved_and_rejects_others():
    ok, bad = ask.verify_citations([{"doc": D, "page": 4}, {"doc": D, "page": 9}], SRC)
    assert ok == [{"doc": D, "page": 4}] and bad == [{"doc": D, "page": 9}]


def test_verify_accepts_bare_pages_for_one_document():
    ok, bad = ask.verify_citations([4, "p.5", "12", None, 4], SRC)
    assert ok == [{"doc": D, "page": 4}, {"doc": D, "page": 5}]
    assert bad == [{"doc": D, "page": 12}]


def test_verify_rejects_bare_pages_when_documents_are_ambiguous():
    src = SRC + [{"doc": "other", "page": 4}]
    ok, bad = ask.verify_citations([4], src)
    assert ok == [] and bad == [{"doc": None, "page": 4}]


def test_verify_handles_empty():
    assert ask.verify_citations(None, SRC) == ([], [])


def test_sources_merge_pages_without_duplicates():
    chunks = [{"doc": D, "pages": [7, 8]}, {"doc": D, "pages": [8]}, {"doc": "b", "pages": [1]}]
    assert ask.sources(chunks) == [{"doc": D, "page": 7}, {"doc": D, "page": 8}, {"doc": "b", "page": 1}]


def test_label():
    assert ask.label({"doc": D, "pages": [4]}) == "report p.4"
    assert ask.label({"doc": D, "pages": [7, 8]}) == "report p.7-8"


def _fake_llm(content):
    usage = SimpleNamespace(total_tokens=30, prompt_tokens=20, completion_tokens=10)
    resp = SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    create = lambda **kw: resp
    return lambda *role: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _patch(monkeypatch, content):
    chunks = [{"doc": D, "pages": [4], "type": "table", "caption": "Table 1", "score": 0.5, "text": "| a |"}]
    logged = []
    monkeypatch.setenv("ANSWER_MODEL", "m")
    monkeypatch.setenv("ROUTER", "0")  # router tests turn it on
    monkeypatch.setattr(ask, "search", lambda q, k=6, doc=None: chunks)
    monkeypatch.setattr(ask, "llm", _fake_llm(content))
    monkeypatch.setattr(ask, "log", lambda **f: logged.append(f))
    return logged


def test_answer_rejects_hallucinated_page_and_logs(monkeypatch):
    raw = json.dumps({"answer": "x [report p.4]", "citations": [{"doc": D, "page": 4}, {"doc": D, "page": 40}],
                      "refused": False})
    logged = _patch(monkeypatch, raw)
    out = ask.answer("q?")
    assert out["citations"] == [{"doc": D, "page": 4}]
    assert out["rejected_citations"] == [{"doc": D, "page": 40}]
    assert (out["prompt_tokens"], out["completion_tokens"]) == (20, 10)
    assert logged[0]["status"] == "ok" and logged[0]["rejected"] == 1


def test_answer_survives_non_json_reply(monkeypatch):
    _patch(monkeypatch, "I think it was 468.")
    out = ask.answer("q?")
    assert out["answer"] == "I think it was 468." and out["citations"] == [] and out["refused"] is False


def test_answer_logs_errors(monkeypatch):
    logged = _patch(monkeypatch, "{}")
    monkeypatch.setattr(ask, "llm", lambda *role: (_ for _ in ()).throw(RuntimeError("proxy down")))
    try:
        ask.answer("q?")
    except RuntimeError:
        pass
    assert logged[0]["status"] == "error" and "proxy down" in logged[0]["error"]


def test_answer_reports_candidates_and_which_were_sent(monkeypatch):
    monkeypatch.setenv("RERANK", "0")
    _patch(monkeypatch, '{"answer": "a", "citations": [], "refused": false}')
    monkeypatch.setattr(ask, "K", 1)
    extra = {"doc": D, "pages": [5], "type": "prose", "caption": "", "score": 0.2, "text": "x"}
    monkeypatch.setattr(ask, "search", lambda q, k=6, doc=None: [
        {"doc": D, "pages": [4], "type": "table", "caption": "Table 1", "score": 0.5, "text": "| a |"}, extra])
    out = ask.answer("q")
    assert [(c["pages"], c["used"]) for c in out["candidates"]] == [([4], True), ([5], False)]
    assert out["retrieved_pages"] == [4] and out["score_kind"] == "cosine"


def test_answer_text_grows_and_never_takes_text_back():
    full = json.dumps({"answer": 'Line\n"q" é \U0001F600 end', "citations": [], "refused": False})
    seen = ""
    for n in range(len(full) + 1):
        text = ask._answer_text(full[:n])
        assert text.startswith(seen)
        seen = text
    assert seen == 'Line\n"q" é \U0001F600 end'
    assert ask._answer_text("no json yet") == ""


def test_answer_stream_yields_candidates_tokens_and_the_final_dict(monkeypatch):
    _patch(monkeypatch, "")
    body = json.dumps({"answer": "Net profit was 5.", "citations": [{"doc": D, "page": 4}], "refused": False})
    usage = SimpleNamespace(total_tokens=30, prompt_tokens=20, completion_tokens=10)
    parts = [body[i:i + 7] for i in range(0, len(body), 7)]
    events = [SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=p))]) for p in parts]
    events.append(SimpleNamespace(usage=usage, choices=[]))  # the usage event has no choices
    seen = {}

    def create(**kw):
        seen.update(kw)
        return iter(events)

    monkeypatch.setattr(ask, "llm", lambda *role: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    out = list(ask.answer_stream("q", model="other"))
    assert out[0] == ("route", "rag") and out[1][0] == "candidates" and out[-1][0] == "done"
    assert "".join(t for k, t in out if k == "token") == "Net profit was 5."
    assert out[-1][1]["tokens"] == 30 and out[-1][1]["citations"] == [{"doc": D, "page": 4}]
    assert seen["model"] == "other" and seen["stream"] is True


def test_stream_falls_back_when_the_provider_rejects_stream_options(monkeypatch):
    from openai import BadRequestError

    _patch(monkeypatch, "")
    body = json.dumps({"answer": "Five.", "citations": [], "refused": False})
    events = [SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=body))])]
    calls = []

    def create(**kw):
        calls.append(kw)
        if "stream_options" in kw:
            raise BadRequestError("unknown parameter", response=SimpleNamespace(status_code=400, headers={}, request=None), body=None)
        return iter(events)

    monkeypatch.setattr(ask, "llm", lambda *role: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    out = list(ask.answer_stream("q"))
    assert out[-1][1]["answer"] == "Five." and out[-1][1]["tokens"] is None  # no usage from this provider
    assert ["stream_options" in c for c in calls] == [True, False]


def _router_llm(route_word, chat_text="Hello! Ask me about your documents."):
    """A fake client: the router call (it sets max_tokens) gets route_word, any other call gets chat_text."""
    usage = SimpleNamespace(total_tokens=9, prompt_tokens=6, completion_tokens=3)

    def create(**kw):
        if kw.get("stream"):
            parts = [chat_text[:6], chat_text[6:]]
            evs = [SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=p))]) for p in parts]
            return iter(evs + [SimpleNamespace(usage=usage, choices=[])])
        text = route_word if "max_tokens" in kw else chat_text
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    return lambda *role: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _router_on(monkeypatch, word):
    _patch(monkeypatch, "")
    monkeypatch.setenv("ROUTER", "1")
    monkeypatch.setattr(ask, "llm", _router_llm(word))


def test_route_reads_one_word_and_defaults_to_rag(monkeypatch):
    for word, want in (("chat", "chat"), (" Chat.", "chat"), ("rag", "rag"), ("", "rag"), ("maybe", "rag")):
        _router_on(monkeypatch, word)
        assert ask.route("hey") == want, word


def test_route_falls_back_to_rag_on_error_and_can_be_turned_off(monkeypatch):
    _patch(monkeypatch, "")
    monkeypatch.setenv("ROUTER", "1")
    monkeypatch.setattr(ask, "llm", lambda *role: (_ for _ in ()).throw(RuntimeError("down")))
    assert ask.route("hey") == "rag"
    monkeypatch.setenv("ROUTER", "0")
    monkeypatch.setattr(ask, "llm", lambda *role: pytest.fail("the router must not call the model when it is off"))
    assert ask.route("hey") == "rag"


def test_a_chat_message_gets_a_reply_without_sources_or_candidates(monkeypatch):
    _router_on(monkeypatch, "chat")
    out = ask.answer("hey")
    assert out["route"] == "chat" and out["answer"].startswith("Hello")
    assert out["citations"] == [] and out["retrieved"] == [] and out["candidates"] == [] and out["refused"] is False


def test_a_document_question_still_searches(monkeypatch):
    _router_on(monkeypatch, "rag")
    monkeypatch.setattr(ask, "llm", _fake_llm('{"answer": "Five.", "citations": [], "refused": false}'))
    monkeypatch.setenv("ROUTER", "0")
    assert ask.answer("net profit?")["route"] == "rag"


def test_chat_streams_tokens_and_sends_no_candidates(monkeypatch):
    _router_on(monkeypatch, "chat")
    ev = list(ask.answer_stream("hey"))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "route" and ev[0][1] == "chat" and kinds[-1] == "done"
    assert "candidates" not in kinds
    assert "".join(d for k, d in ev if k == "token") == "Hello! Ask me about your documents."
    assert ev[-1][1]["route"] == "chat" and ev[-1][1]["tokens"] == 9


def test_router_leaves_room_for_a_reasoning_model(monkeypatch):
    _patch(monkeypatch, "")
    monkeypatch.setenv("ROUTER", "1")
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="chat"))])

    monkeypatch.setattr(ask, "llm", lambda *role: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    ask.route("hey")
    assert seen["max_tokens"] >= 64  # 8 tokens gave an empty reply from deepseek-flash, which thinks first
