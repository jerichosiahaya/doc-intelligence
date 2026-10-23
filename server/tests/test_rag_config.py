import pytest

import rag

KEYS = ["LLM_BASE_URL", "LLM_API_KEY", "LITELLM_URL", "LITELLM_KEY",
        "ANSWER_BASE_URL", "ANSWER_API_KEY", "VISION_BASE_URL", "VISION_API_KEY", "JUDGE_BASE_URL", "JUDGE_API_KEY"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    rag._clients.clear()


def test_one_endpoint_serves_every_role(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "k1")
    assert rag.llm("answer") is rag.llm("judge")
    assert str(rag.llm("answer").base_url).startswith("https://api.deepseek.com")


def test_a_role_can_use_its_own_provider(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_API_KEY", "k1")
    monkeypatch.setenv("VISION_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("VISION_API_KEY", "k2")
    assert str(rag.llm("vision").base_url).startswith("https://api.openai.com")
    assert rag.llm("vision").api_key == "k2"
    assert rag.llm("answer").api_key == "k1"


def test_the_old_litellm_settings_still_work(monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "https://proxy.example/v1")
    monkeypatch.setenv("LITELLM_KEY", "old")
    assert rag.llm("vision").api_key == "old"


def test_a_missing_endpoint_says_what_to_set():
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        rag.llm("answer")
