"""History against a real Postgres. Run with DATABASE_URL set (see docker-compose.yml)."""
import uuid

import pytest

import history

pytestmark = pytest.mark.db

REPLY = {"answer": "THB 468 million [r p.4]", "citations": [{"doc": "r", "page": 4}], "rejected_citations": [],
         "retrieved": [{"doc": "r", "page": 4}], "refused": False, "seconds": 1.2, "tokens": 900}


@pytest.fixture(scope="module", autouse=True)
def schema():
    history.init()
    history.init()  # running it twice must be harmless


@pytest.fixture
def made():
    ids = []
    yield ids
    for i in ids:
        history.delete_conversation(i)


def test_new_conversation_gets_a_trimmed_title_and_two_messages(made):
    cid = history.save_turn("  What was net profit\n in 2024?  " + "x" * 100, REPLY, doc="r")
    made.append(cid)
    conv = history.get_conversation(cid)
    assert conv["title"].startswith("What was net profit in 2024? xxx") and len(conv["title"]) == history.TITLE_CHARS
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    user, assistant = conv["messages"]
    assert user["doc"] == "r" and user["citations"] is None
    assert assistant["citations"] == REPLY["citations"] and assistant["tokens"] == 900 and assistant["refused"] is False


def test_follow_up_goes_to_the_same_conversation_in_order(made):
    cid = history.save_turn("first?", REPLY)
    made.append(cid)
    before = history.get_conversation(cid)["updated_at"]
    assert history.save_turn("second?", REPLY, conversation_id=cid) == cid
    conv = history.get_conversation(cid)
    assert [m["content"] for m in conv["messages"] if m["role"] == "user"] == ["first?", "second?"]
    assert conv["updated_at"] > before


def test_list_is_newest_first_with_message_counts(made):
    a = history.save_turn("older", REPLY)
    b = history.save_turn("newer", REPLY)
    made += [a, b]
    history.save_turn("older again", REPLY, conversation_id=a)  # touching a makes it the newest
    rows = [r for r in history.list_conversations() if r["id"] in (a, b)]
    assert [r["id"] for r in rows] == [a, b]
    assert rows[0]["messages"] == 4 and rows[1]["messages"] == 2


def test_unknown_conversation(made):
    ghost = uuid.uuid4()
    assert not history.exists(ghost)
    assert history.get_conversation(ghost) is None
    with pytest.raises(history.UnknownConversation):
        history.save_turn("x", REPLY, conversation_id=ghost)
    assert not history.delete_conversation(ghost)


def test_delete_removes_messages_too():
    cid = history.save_turn("bye", REPLY)
    assert history.delete_conversation(cid)
    with history.connect() as c:
        assert c.execute("SELECT count(*) n FROM messages WHERE conversation_id = %s", (cid,)).fetchone()["n"] == 0


def test_parse_id():
    assert history.parse_id(str(uuid.uuid4()))
    with pytest.raises(ValueError):
        history.parse_id("not-a-uuid")
