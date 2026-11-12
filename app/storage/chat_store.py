"""
Persistent chat history for SAP AI Copilot.

Conversations are stored in a dedicated SQLite file (data/chat_history.db),
kept separate from the SAP business database (data/sap.db).
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_DB_PATH = PROJECT_ROOT / "data" / "chat_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    tools_used TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id)
        REFERENCES conversations(session_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, message_id);
"""

TITLE_MAX_LENGTH = 60


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect():
    """Open a connection, commit on success, roll back on error, always close."""

    CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(CHAT_DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with _connect() as connection:
        connection.executescript(SCHEMA)

        # Additive migration: module workspace of a conversation (NULL = general)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(conversations)")}
        if "module" not in columns:
            connection.execute("ALTER TABLE conversations ADD COLUMN module TEXT")


def build_title(message: str) -> str:
    title = " ".join(message.split())

    if len(title) > TITLE_MAX_LENGTH:
        title = title[:TITLE_MAX_LENGTH - 1].rstrip() + "…"

    return title or "New conversation"


def conversation_exists(session_id: str) -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM conversations WHERE session_id = ?",
            (session_id,)
        ).fetchone()

    return row is not None


def save_exchange(
    session_id: str,
    user_message: str,
    assistant_message: str,
    sources: list,
    tools_used: list,
    module: str | None = None
):
    """Store one user/assistant exchange, creating the conversation if needed."""

    now = _now()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO conversations (session_id, title, created_at, updated_at, module)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, build_title(user_message), now, now, module)
        )

        connection.executemany(
            """
            INSERT INTO messages
                (session_id, role, content, sources, tools_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (session_id, "user", user_message, "[]", "[]", now),
                (
                    session_id,
                    "assistant",
                    assistant_message,
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps(tools_used, ensure_ascii=False),
                    now
                )
            ]
        )


def list_conversations():
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                c.session_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.module,
                COUNT(m.message_id) AS message_count
            FROM conversations c
            LEFT JOIN messages m
                ON m.session_id = c.session_id
            GROUP BY c.session_id
            ORDER BY c.updated_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_conversation(session_id: str):
    with _connect() as connection:
        conversation = connection.execute(
            """
            SELECT session_id, title, created_at, updated_at, module
            FROM conversations
            WHERE session_id = ?
            """,
            (session_id,)
        ).fetchone()

        if conversation is None:
            return None

        rows = connection.execute(
            """
            SELECT role, content, sources, tools_used, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY message_id ASC
            """,
            (session_id,)
        ).fetchall()

    result = dict(conversation)
    result["messages"] = [
        {
            "role": row["role"],
            "content": row["content"],
            "sources": json.loads(row["sources"]),
            "tools_used": json.loads(row["tools_used"]),
            "created_at": row["created_at"]
        }
        for row in rows
    ]

    return result


def rename_conversation(session_id: str, title: str) -> bool:
    """Change a conversation title (normalised like generated titles)."""

    with _connect() as connection:
        cursor = connection.execute(
            "UPDATE conversations SET title = ? WHERE session_id = ?",
            (build_title(title), session_id)
        )

    return cursor.rowcount > 0


def delete_conversation(session_id: str) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,)
        )

    return cursor.rowcount > 0
