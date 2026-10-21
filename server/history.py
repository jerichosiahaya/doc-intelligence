"""Conversation history in Postgres. Off unless DATABASE_URL is set.

A conversation holds messages: each question is a 'user' message and each answer an
'assistant' message with its citations. The server saves turns; the CLI does not.
"""
import os
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
    id                  bigserial PRIMARY KEY,
    conversation_id     uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role                text NOT NULL CHECK (role IN ('user', 'assistant')),
    content             text NOT NULL,
    doc                 text,
    citations           jsonb,
    rejected_citations  jsonb,
    retrieved           jsonb,
    refused             boolean,
    seconds             real,
    tokens              integer,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS conversations_updated_idx ON conversations (updated_at DESC);
"""

TITLE_CHARS = 60


class UnknownConversation(Exception):
    pass


def enabled():
    return bool(os.environ.get("DATABASE_URL"))


def connect():
    # ponytail: one connection per call; add psycopg_pool if request volume grows
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init():
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(727274)")  # two servers starting together must not race
        c.execute(SCHEMA)


def exists(conversation_id):
    with connect() as c:
        return c.execute("SELECT 1 FROM conversations WHERE id = %s", (conversation_id,)).fetchone() is not None


def save_turn(question, reply, doc=None, conversation_id=None):
    """Save one question and its answer. Starts a new conversation when conversation_id is None.
    reply is the dict form of the answer (answer, citations, rejected_citations, retrieved, ...)."""
    with connect() as c:  # one transaction: both messages or neither
        if conversation_id is None:
            title = " ".join(question.split())[:TITLE_CHARS]
            conversation_id = c.execute(
                "INSERT INTO conversations (title) VALUES (%s) RETURNING id", (title,)).fetchone()["id"]
        elif not c.execute("UPDATE conversations SET updated_at = now() WHERE id = %s RETURNING 1",
                           (conversation_id,)).fetchone():
            raise UnknownConversation(str(conversation_id))
        c.execute("INSERT INTO messages (conversation_id, role, content, doc) VALUES (%s, 'user', %s, %s)",
                  (conversation_id, question, doc))
        c.execute(
            "INSERT INTO messages (conversation_id, role, content, doc, citations, rejected_citations, retrieved,"
            " refused, seconds, tokens) VALUES (%s, 'assistant', %s, %s, %s, %s, %s, %s, %s, %s)",
            (conversation_id, reply["answer"], doc, Jsonb(reply["citations"]), Jsonb(reply["rejected_citations"]),
             Jsonb(reply["retrieved"]), reply["refused"], reply["seconds"], reply["tokens"]))
        return conversation_id


def list_conversations(limit=50):
    with connect() as c:
        return c.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, count(m.id)::int AS messages"
            " FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id"
            " GROUP BY c.id ORDER BY c.updated_at DESC LIMIT %s", (limit,)).fetchall()


def get_conversation(conversation_id):
    with connect() as c:
        conv = c.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE id = %s",
                         (conversation_id,)).fetchone()
        if not conv:
            return None
        conv["messages"] = c.execute(
            "SELECT id, role, content, doc, citations, rejected_citations, retrieved, refused, seconds, tokens,"
            " created_at FROM messages WHERE conversation_id = %s ORDER BY id", (conversation_id,)).fetchall()
        return conv


def delete_conversation(conversation_id):
    with connect() as c:
        return c.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,)).rowcount > 0


def parse_id(value):
    """Validate a conversation id from a client. Raises ValueError for anything that is not a UUID."""
    return uuid.UUID(str(value))
